"""Record loop (Tier 1) — drive an app with an agent and emit a scenario.

observe (query) -> agent proposes the next action -> execute it -> repeat, until
the agent signals done or max_steps is hit. The recorded steps form a deterministic
scenario that `run` later replays with no AI.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Literal

from bajutsu.agents.protocols import Agent, HumanValueClass, Observation, Proposal
from bajutsu.analytics import usage as _usage
from bajutsu.crawl import screen_identity
from bajutsu.drivers import base
from bajutsu.elements import shows_app_ui
from bajutsu.handoff import Handoff, HandoffRequest, HumanHandoffUnavailable
from bajutsu.orchestrator import BlockedHandler, Clock, RealClock, _action_of, _do_action, _wait
from bajutsu.orchestrator.types import SelectionState
from bajutsu.scenario import Assertion, Scenario, Selector, Step
from bajutsu.scenario.models.actions import bypass_hint
from bajutsu.screenshots import screenshot_bytes

_logger = logging.getLogger(__name__)

# A live-progress sink: each turn's decision is handed to it as a one-line string.
Reporter = Callable[[str], None]


def _format_elapsed(seconds: float) -> str:
    """Wall-clock duration as a compact string — `13.4s`, or `2m 03s` past a minute."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s"


def _describe_selector(sel: Selector | None) -> str:
    """A compact human label for a selector — id if present, else label/value/traits[index]."""
    if sel is None:
        return "?"
    if sel.id:
        return f"#{sel.first_id()}"  # primary candidate, not a raw list, for a candidate-list id
    parts = []
    if sel.label is not None:
        parts.append(f"label={sel.label!r}")
    if sel.value is not None:
        parts.append(f"value={sel.value!r}")
    if sel.traits:
        parts.append(f"traits={sel.traits}")
    if sel.index is not None:
        parts.append(f"index={sel.index}")
    return " ".join(parts) or "?"


def _summarize_screen(elements: list[base.Element]) -> str:
    """A one-line summary of the screen for a handoff request (a terminal responder has no image)."""
    labels = [
        label
        for e in elements
        if (label := str(e.get("label") or e.get("identifier") or "").strip())
    ]
    tail = ", …" if len(labels) > 8 else ""
    listed = f": {', '.join(labels[:8])}{tail}" if labels else ""
    return f"{len(elements)} element(s) on screen{listed}"


def describe_step(step: Step) -> str:
    """A one-line summary of a proposed step, for live record output."""
    if step.tap is not None:
        return f"tap {_describe_selector(step.tap)}"
    if step.tap_point is not None:
        return f"tap point ({step.tap_point.x:.2f}, {step.tap_point.y:.2f}) [located in screenshot]"
    if step.swipe is not None and step.swipe.direction is not None:
        extent = f" {step.swipe.amount:.0%}" if step.swipe.amount is not None else ""
        return f"swipe {step.swipe.direction}{extent} on {_describe_selector(step.swipe.on)}"
    if step.type is not None:
        return f"type {step.type.text!r} into {_describe_selector(step.type.into)}"
    if step.wait is not None:
        return f"wait for {_describe_selector(step.wait.for_)}"
    if step.manual is not None:
        return f"manual takeover: {step.manual.label}"
    return next((f for f in step.model_dump(exclude_none=True)), "step")


def _is_looping(signatures: list[str]) -> bool:
    """Whether the recorded steps show the agent stuck rather than progressing.

    Two deterministic patterns (no model — this must never gate on an LLM): the same action three
    times running, or a two-step A,B,A,B oscillation (the classic open-modal / close-modal cycle the
    agent falls into when a control it wants isn't where it expects). Stopping on either turns a
    silent, expensive spin (dozens of model calls) into an actionable, bounded outcome.
    """
    if len(signatures) >= 3 and signatures[-1] == signatures[-2] == signatures[-3]:
        return True
    return (
        len(signatures) >= 4
        and signatures[-1] == signatures[-3]
        and signatures[-2] == signatures[-4]
        and signatures[-1] != signatures[-2]
    )


def _mask_secrets(text: str, secret_tokens: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Replace each declared secret literal in `text` with its `${secrets.X}` token.

    `secret_tokens` is `(literal value, "${secrets.NAME}")` pairs; the caller passes them
    longest-value-first so a value that is a substring of another is replaced before it, never
    leaving a partial literal behind. Returns the masked text and the tokens substituted.

    Done in two passes — each matched value is first swapped for a collision-proof sentinel, then
    the sentinels are expanded to their tokens — so a later value can never match text *inside* a
    token already inserted (e.g. a secret whose value equals another secret's env-var name), which
    a single sequential pass would corrupt into a malformed nested token.
    """
    substituted: list[str] = []
    expansions: list[tuple[str, str]] = []
    for i, (value, token) in enumerate(secret_tokens):
        if value and value in text:
            sentinel = f"\x00{i}\x00"  # NUL-delimited: cannot occur in typed text or a token
            text = text.replace(value, sentinel)
            expansions.append((sentinel, token))
            substituted.append(token)
    for sentinel, token in expansions:
        text = text.replace(sentinel, token)
    return text, substituted


def _tokenize_secrets(step: Step, secret_tokens: list[tuple[str, str]]) -> tuple[Step, list[str]]:
    """Rewrite a recorded secret literal in a `type` step's text as its `${secrets.X}` token.

    A non-`type` step, or a `type` text containing no declared secret, is returned unchanged.
    Returns the (possibly rewritten) step and the tokens substituted, so the record loop can tell
    the author which fields were swapped.
    """
    if step.type is None or not secret_tokens:
        return step, []
    text, substituted = _mask_secrets(step.type.text, secret_tokens)
    if not substituted:
        return step, []
    return step.model_copy(
        update={"type": step.type.model_copy(update={"text": text})}
    ), substituted


def _placeholder_name(name: str | None, field: Selector | None) -> str:
    """A safe `${vars/secrets.*}` placeholder name for a human-supplied value (BE-0182).

    Prefer the agent's suggested name, else the target field's id, else a generic fallback; keep
    only identifier-safe characters so the emitted token round-trips through interpolation.
    """
    raw = name or (field.first_id() if field is not None else None) or "human_value"
    cleaned = re.sub(r"\W+", "_", raw).strip("_")
    return cleaned or "human_value"


def _unique_name(base: str, used: set[str]) -> str:
    """Disambiguate a placeholder name against ones already emitted this recording (BE-0182).

    Two human-value handoffs that derive the same fallback name (e.g. both idless and unnamed)
    would otherwise alias to one `${vars.*}` token, silently binding two distinct values together.
    A repeat gets a numeric suffix; `used` is updated with the chosen name.
    """
    name = base
    i = 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def _human_value_step(
    field: Selector, classify: HumanValueClass | None, name: str
) -> tuple[Step, str]:
    """The placeholder `type` step recorded for a human value, plus its one-line TODO (BE-0182).

    The literal the human supplied is never written into the artifact: the step carries a
    `${vars.*}` placeholder — produced at run time by a `totp` / `email` step (BE-0046) — or a
    `${secrets.*}` placeholder for a declared secret, chosen by the agent's *proposed* `classify`
    (authoring, not judging; the author confirms and wires it). The `from:` provenance (BE-0044)
    records the human-value origin and the classification so a report or the GUI editor can show
    why the step still needs wiring.
    """
    if classify == "secret":
        placeholder = f"${{secrets.{name}}}"
        todo = f"human value entered during record — declare {name} as a secret ({placeholder})"
    else:
        placeholder = f"${{vars.{name}}}"
        if classify in ("totp", "email"):
            bridge = "an email step" if classify == "email" else "a totp step"
            todo = (
                f"human value entered during record — resolve {placeholder} with {bridge} (BE-0046)"
            )
        else:
            # The agent proposed no (recognized) classification: don't assert a totp step it never
            # chose — leave a neutral TODO so the author picks the right run-time source.
            todo = (
                f"human value entered during record — classify {placeholder} and resolve it "
                "(a totp / email step, BE-0046, or declare it a secret)"
            )
    step = Step.model_validate(
        {
            "type": {
                "into": field.model_dump(exclude_none=True, by_alias=True),
                "text": placeholder,
            },
            "from": todo,
        }
    )
    return step, todo


def _manual_takeover_step(label: str, bypass: str | None) -> tuple[Step, str]:
    """The `manual` marker recorded for a human takeover, plus its one-line TODO (BE-0185).

    An operation the AI could not perform (a CAPTCHA, a biometric prompt): the human acted live and
    this records a marker of the observed transition, never the raw gesture. `bypass` — proposed by
    the agent, confirmed by the author — names a deterministic bridge to wire (a test-build flag, a
    device-control / device-state primitive, BE-0035 / BE-0052) so `run` becomes deterministic; None
    leaves an honest, unreproducible marker that fails loudly at run time rather than faking a pass.
    """
    if bypass:
        todo = f"human takeover during record — {bypass_hint(bypass)} (BE-0035 / BE-0052)"
    else:
        todo = (
            f"human takeover during record — {bypass_hint(bypass)}; the step fails at "
            "run time until a bypass is wired or it is handled out of band"
        )
    step = Step.model_validate({"manual": {"label": label, "bypass": bypass}, "from": todo})
    return step, todo


def _offer_takeover(
    handoff: Handoff,
    *,
    reason: str,
    elements: list[base.Element],
    screenshot: bytes | None,
    bypass: str | None,
) -> tuple[Step, str] | Literal["value_dropped"] | None:
    """Offer a takeover handoff and, if the human operated the device, return its `manual` marker.

    The human operates the live device directly — bajutsu never drives — and resumes; this returns the
    (`manual` step, TODO) recording the observed transition when they acted, `"value_dropped"` when the
    human supplied a value that has no field to record into (so the caller can emit a distinct message),
    or None when they cancelled, dismissed the pane without acting, or the responder timed out — in
    which case the caller stops.
    This mirrors the *shape* of the agent-signalled takeover branch in the main loop (BE-0185) — the
    same `request` + `HandoffResponse.kind` precedence — but does not share its code: that branch inlines
    the check to keep its own bypass-masking and value-drop handling. The loop-detected trigger passes
    `bypass=None`, so the marker is an honest, unreproducible one that fails loudly at run time.
    """
    response = handoff.request(
        HandoffRequest(
            reason=reason,
            screen=_summarize_screen(elements),
            screenshot=screenshot,
        )
    )
    # `kind` resolves the precedence once (cancel > value > acted) so a response carrying more than one
    # field can't bypass a cancel — the same ordering the needs_human takeover branch relies on.
    if response.kind == "cancel":
        return None
    if response.kind == "value":
        return "value_dropped"
    # `kind` defaults to "acted" whenever nothing is set, so gate on the explicit flag: a bare resume
    # (the human dismissed the pane without operating the device) must not fabricate a `manual` marker
    # for a takeover that never happened — the same guard the needs_human branch keys on the flag for.
    if not response.acted:
        return None
    return _manual_takeover_step(reason, bypass)


def _should_attach(current: str, previous: str | None) -> bool:
    """Whether this turn's observation should carry a screenshot (BE-0192, vision-on-demand).

    `current`/`previous` are `crawl.screen_identity(...)` signatures — the same transition signature
    the batch-abort check uses. Two deterministic triggers over the element tree — no model, so
    `record` stays Tier 1 (prime directive 1):

    - **New-screen**: the current signature differs from the previous turn's, or it is the first
      turn. The agent has not seen this *view* yet, so it gets the image. `screen_identity` strips
      per-element interactive state (a field's fill, a control's enabled/selected flags), so merely
      typing into a field or toggling a control on the same view does not force a re-attach — the
      trigger fires on a genuine view change, which is where the token saving comes from.
    - **Degenerate-tree**: the signature took `screen_identity`'s structural path (prefixed
      `structural:`) — too few accessibility identifiers to address by selector, the no-id, tab-bar
      case where `tap_point` is the expected path. The image is attached proactively (deliberately
      generous, so an id-poor screen never relies on an escalation round-trip).

    A view already seen whose tree is addressable by id fires neither trigger, and its turn is
    text-only — the element list alone determines the action there.
    """
    if previous is None or current != previous:
        return True
    return current.startswith("structural:")


def _ask_agent(agent: Agent, observation: Observation, say: Reporter, turn: int) -> Proposal:
    """Ask the agent for its next move, streaming this turn's token cost.

    Factored out so the escalation path (BE-0192) can re-issue the same turn with a screenshot
    attached without duplicating the single-threaded token accounting.
    """
    before = _usage.snapshot()
    proposal = agent.next_action(observation)
    spent = _usage.snapshot() - before  # single-threaded record loop → this turn's tokens exactly
    if spent.total_tokens:
        say(f"[{turn}] \U0001f916 agent replied · {spent.total_tokens:,} tokens")
    return proposal


def _settle_target(assertion: Assertion) -> base.Selector | None:
    """The selector of a positive-existence assertion (something that must be present)."""
    if assertion.exists is not None and not assertion.exists.negate:
        return assertion.exists.sel.as_selector()
    if assertion.value is not None:
        return assertion.value.sel.as_selector()
    if assertion.label is not None:
        return assertion.label.sel.as_selector()
    for state in (assertion.enabled, assertion.disabled, assertion.selected):
        if state is not None:
            return state.as_selector()
    return None  # notExists / count: no single element to wait for


def settle_step(expect: list[Assertion], timeout: float = 5.0) -> Step | None:
    """A wait for the first asserted element, recorded before the assertions.

    The agent observes a settled screen between turns, but deterministic replay runs
    fast and can verify before an async transition (e.g. a sheet) has rendered. A wait
    for an asserted element makes the recorded scenario self-sufficient without adding
    implicit timing to `run`.
    """
    for assertion in expect:
        target = _settle_target(assertion)
        if target is not None:
            return Step.model_validate({"wait": {"for": target, "timeout": timeout}})
    return None


def execute(
    driver: base.Driver,
    step: Step,
    clock: Clock,
    on_wait_failure: Callable[[str], None] | None = None,
    selection: SelectionState | None = None,
) -> None:
    """Replay one authored step: run a wait/action, treat an assertion as a no-op.

    Shared by `record` and `enrich` (BE-0201). The two paths differ only in how a timed-out
    `wait` is handled: `record` records forward regardless (default `on_wait_failure=None`, so
    `_wait`'s failure result is dropped), while `enrich` passes a hook that raises `_ReplayFailed`
    so a step it cannot settle stops the replay.

    `selection` carries the live text selection across steps for `copy` (BE-0265), the same state
    the run loop threads: a caller replaying a sequence passes one shared instance so a `select`
    step establishes the selection a later `copy` copies.
    """
    kind = _action_of(step)
    if kind == "wait":
        assert step.wait is not None
        ok, reason, _ = _wait(driver, step.wait, clock)
        if not ok and on_wait_failure is not None:
            on_wait_failure(reason)
    elif kind == "assert_":
        return  # assertions are checks, not actions to perform while recording
    else:
        _do_action(driver, step, selection=selection)


def clear_blocking(
    driver: base.Driver,
    guard: BlockedHandler,
    clock: Clock,
    max_tries: int = 3,
    report: Reporter | None = None,
) -> list[str]:
    """Dismiss anything covering the app (e.g. a system alert) before the agent observes.

    Return the dismiss-button label(s) tapped (empty if nothing was blocking).

    A SpringBoard alert has no queryable app content and collapses the tree to a bare
    window — leaving the agent nothing to act on. While the tree stays collapsed, keep
    asking the guard to clear it: an alert caught mid-animation can be missed on the first
    screenshot. When `report` is given, the guard's detection and dismissal are streamed
    so the watcher sees it stepping in.
    """
    say = report or (lambda _msg: None)
    dismissed: list[str] = []
    announced = False
    for _ in range(max_tries):
        if shows_app_ui(driver.query()):
            break  # the app is showing actionable elements; nothing (more) blocking
        if not announced:
            say(
                "⚠️  the app screen looks blocked by a system prompt — asking the alert guard to clear it …"
            )
            announced = True
        event = guard(driver)  # try to dismiss whatever is covering the app
        if event is not None:
            label = getattr(event, "label", "")
            dismissed.append(label)
            say(
                f"🛡️  dismissed a system alert · tapped {label!r}"
                if label
                else "🛡️  dismissed a system alert"
            )
        clock.sleep(0.5)  # let it animate out before re-checking
    return dismissed


def _execute_with_recovery(
    driver: base.Driver,
    step: Step,
    clock: Clock,
    guard: BlockedHandler | None,
    report: Reporter | None = None,
    selection: SelectionState | None = None,
) -> bool:
    """Execute a step; if it fails because a prompt is covering the app, clear it and retry."""
    try:
        execute(driver, step, clock, selection=selection)
        return True
    except base.SelectorError:
        if guard is None:
            return False
        (report or (lambda _m: None))(
            "⚠️  a step could not act — a system prompt may be covering the app; recovering …"
        )
        clear_blocking(driver, guard, clock, report=report)
        try:
            execute(driver, step, clock, selection=selection)
            return True
        except base.SelectorError:
            return False


def _plan_goal(agent: Agent, goal: str, say: Reporter) -> list[str]:
    """Decompose the goal into concrete steps up front and stream them to the watcher.

    Best-effort: an agent without a `plan` method (e.g. a test fake) or a planning call that
    fails just yields no plan — the loop then runs exactly as before. When a plan is produced
    it is both explained here and fed back to the agent each turn via `Observation.plan`.
    """
    planner = getattr(agent, "plan", None)
    if planner is None:
        return []
    # Announce before the call, not after: planning is a (slow) LLM round-trip, so without
    # this the watcher stares at a silent screen between the goal and the plan appearing.
    say("\U0001f9ed thinking about how to approach the goal …")
    try:
        plan = [str(step) for step in planner(goal)]
    except Exception as exc:
        say(f"… could not plan the goal up front ({exc}); proceeding step by step")
        return []
    if plan:
        say(f"\U0001f5fa️  plan — {goal}")
        for i, step in enumerate(plan, 1):
            say(f"   {i}. {step}")
    return plan


def record(
    driver: base.Driver,
    goal: str,
    agent: Agent,
    *,
    name: str = "recorded",
    max_steps: int = 30,
    clock: Clock | None = None,
    with_screenshot: bool = True,
    alert_guard: BlockedHandler | None = None,
    secret_tokens: list[tuple[str, str]] | None = None,
    capture_video: bool = False,
    report: Reporter | None = None,
    handoff: Handoff | None = None,
) -> Scenario:
    """Explore toward `goal` with `agent`, returning the recorded scenario.

    If `alert_guard` is given, an unexpected OS prompt (e.g. iOS "Save Password?")
    that surfaces while authoring is dismissed so the agent keeps a clean view. The
    dismissal is environmental, not a recorded step; replay handles it with
    `run --alert-handling`.

    If `secret_tokens` is given (`(literal value, "${secrets.NAME}")` pairs, longest-value-first),
    a typed value matching a declared secret is recorded as its `${secrets.X}` token, never the
    literal (BE-0120); the app is still driven with the real value so the authenticated screen is
    reached. Empty/None records every value verbatim.

    If `capture_video` is set, the recorded scenario requests a scenario-wide screen video
    (`capture: [video]` on its first step) so a replay records the run — enabled for mobile
    (iOS-simulator) targets, where the recording is a `simctl` interval (BE-0028).

    If `report` is given, each turn's decision (the agent's proposed action and reason)
    is streamed to it as a one-line string, so a caller can show progress live.

    If `handoff` is given, a turn whose outcome is "needs human" (the agent cannot proceed) is
    handed to it — the human supplies a value or performs an operation, and the loop resumes by
    re-observing the live screen (BE-0179). Without a `handoff`, that outcome is a clean, labeled
    failure (`HumanHandoffUnavailable`), never a hang or an AI guess — so `record` stays
    deterministic under CI. The human is only ever in the authoring loop, never on the `run` path.
    """
    clock = clock or RealClock()
    say = report or (lambda _msg: None)
    started = time.monotonic()  # wall-clock: how long the author actually waited (model + device)
    steps: list[Step] = []
    expect: list[Assertion] = []
    # One selection carried across the whole recording, the same contract the run loop threads
    # (BE-0265): a `select` step the agent proposes establishes the selection a later `copy` copies.
    selection = SelectionState()
    # Placeholder names already emitted for human values this recording (BE-0182), so a second
    # idless/unnamed handoff doesn't alias onto the first's `${vars.*}` token.
    used_value_names: set[str] = set()
    plan = _plan_goal(agent, goal, say)
    plan_cursor = 0  # plan steps reached so far — drives the pre-observe "next" hint below
    prev_screen: str | None = (
        None  # previous turn's screen_identity signature (BE-0192 attach trigger)
    )

    for _ in range(max_steps):
        n = len(steps) + 1
        if plan:
            # Before observing, name the step the loop is about to work toward. The concrete action
            # is decided from the live screen (so it can't be printed yet), but the plan says where
            # the run is headed — otherwise the watcher stares at a silent model round-trip with no
            # idea what it is doing. `plan_cursor` advances as the agent attributes actions to steps.
            upcoming = min(plan_cursor, len(plan) - 1)
            say(f"[{n}] ⏭️  next — plan {upcoming + 1}/{len(plan)}: {plan[upcoming]}")
        if alert_guard is not None:
            clear_blocking(driver, alert_guard, clock, report=report)
        elements = driver.query()
        if alert_guard is not None and not shows_app_ui(elements):
            # A prompt slipped in after the last clear: don't ask the agent to act on a
            # dead screen (it would hallucinate ids); re-clear on the next iteration.
            clock.sleep(0.3)
            continue
        say(f"[{n}] observing {len(elements)} elements; asking the agent (waits on the model) …")
        # Vision-on-demand (BE-0192): decide per turn whether to attach a screenshot, then capture
        # it LAZILY — only when a trigger (or the escalation below) actually needs it, so a text-only
        # turn skips the `screenshot` subprocess too. `with_screenshot=False` (a driver with no
        # screenshot capability) keeps every turn text-only, exactly as before.
        current_screen = screen_identity(elements)
        attach = with_screenshot and _should_attach(current_screen, prev_screen)
        prev_screen = current_screen
        screenshot = screenshot_bytes(driver) if attach else None
        proposal = _ask_agent(
            agent,
            Observation(
                goal=goal,
                screen=elements,
                history=list(steps),
                screenshot=screenshot,
                plan=plan,
                vision_available=with_screenshot,
            ),
            say,
            n,
        )
        if proposal.need_screenshot and with_screenshot and screenshot is None:
            # Escalation (BE-0192): the agent could not proceed from the elements alone on a
            # text-only turn. Capture the screen now and re-issue the SAME, unchanged observation
            # once with the image attached — the loop (not an LLM) re-issues, so this stays Tier 1.
            # At most one extra round-trip per turn; a second need_screenshot is ignored (the image
            # is now present) and falls through to the no-action stop below.
            say(
                f"[{n}] \U0001f441️  the agent asked to see the screen; re-observing with a screenshot"
            )
            screenshot = screenshot_bytes(driver)
            proposal = _ask_agent(
                agent,
                Observation(
                    goal=goal,
                    screen=elements,
                    history=list(steps),
                    screenshot=screenshot,
                    plan=plan,
                    vision_available=with_screenshot,
                ),
                say,
                n,
            )
        if proposal.needs_human:
            # A third outcome (BE-0179): the agent cannot proceed and needs a human. Hand off if a
            # responder is present, then resume by re-observing; otherwise fail cleanly and labeled
            # so CI never hangs and the AI never guesses. `continue` re-observes without consuming a
            # step number, and is bounded by the enclosing `max_steps` loop.
            # Mask any declared secret literal before the reason is streamed / logged / raised, the
            # same way the normal step's intent is masked (BE-0120) — a handoff prompt must not leak
            # a secret into the terminal, the serve stream, or CI output.
            reason = _mask_secrets(
                proposal.human_prompt or proposal.note or "the agent cannot proceed without help",
                secret_tokens or [],
            )[0]
            if handoff is None:
                say(
                    f"[{n}] ✋ needs human handoff: {reason} — no responder; re-record interactively"
                )
                raise HumanHandoffUnavailable(reason)
            say(f"[{n}] ✋ pausing for a human — {reason}")
            # Name the field the value goes into when the agent flagged one (BE-0182), so the human
            # knows where the value lands.
            target = (
                f"the {_describe_selector(proposal.human_field)} field"
                if proposal.human_field is not None
                else ""
            )
            response = handoff.request(
                HandoffRequest(
                    reason=reason,
                    screen=_summarize_screen(elements),
                    target=target,
                    screenshot=screenshot,
                )
            )
            if response.kind == "cancel":
                say(f"[{n}] ✋ handoff cancelled; stopping")
                break
            if response.kind == "value" and proposal.human_field is not None:
                # The value pattern (BE-0182): type the human's value into the live field so the
                # recording proceeds, but record a deterministic placeholder step — never the literal
                # (random/secret). Execute the real value against the app, then append the tokenized
                # placeholder step with its classified TODO. If the field no longer resolves, fall
                # through to re-observe rather than record a step that never ran.
                value = response.values[0]
                real_step = Step.model_validate(
                    {
                        "type": {
                            "into": proposal.human_field.model_dump(
                                exclude_none=True, by_alias=True
                            ),
                            "text": value,
                        }
                    }
                )
                if _execute_with_recovery(
                    driver, real_step, clock, alert_guard, report=report, selection=selection
                ):
                    # Reserve the placeholder name only now the type succeeded — a failed resolve
                    # above records no step, so it must not consume a name (BE-0182). This is a
                    # local; NOT the outer `name` (the scenario name), which the loop must preserve.
                    placeholder_name = _unique_name(
                        _placeholder_name(proposal.human_var, proposal.human_field),
                        used_value_names,
                    )
                    placeholder_step, todo = _human_value_step(
                        proposal.human_field, proposal.human_classify, placeholder_name
                    )
                    steps.append(placeholder_step)
                    # Narrate the placeholder and the TODO, never the value — the same no-leak
                    # guarantee the normal typed-secret path keeps (BE-0120).
                    say(
                        f"[{len(steps)}] ✋ recorded human value as {describe_step(placeholder_step)}"
                    )
                    say(f"[{len(steps)}] 📝 {todo}")
                    continue
                # The field did not resolve to a unique element — it vanished, or the agent's
                # selector is ambiguous (prime directive 2). Either way nothing was typed and no
                # step is recorded; re-observe rather than record an action that never ran.
                say(f"[{n}] ✋ could not resolve that field on the live screen; re-observing")
                continue
            if response.acted and not response.values:
                # The takeover pattern (BE-0185): the human operated the device for an operation the
                # AI cannot perform (a CAPTCHA, a biometric prompt). Record a `manual` marker of the
                # transition — not the opaque gesture — classified by whether the agent proposed a
                # deterministic bypass. It fails loudly at run time rather than faking a pass; the
                # author wires the bypass (BE-0035 / BE-0052) or handles it out of band.
                # Gate on the explicit `acted` flag, not `kind == "acted"` — `kind` returns "acted"
                # as its no-cancel/no-value default, so a bare resume (an empty response) must stay a
                # plain re-observe and never fabricate a run-failing marker for a human who did nothing.
                # Also require an empty `values`: a response can carry both a value and `acted` (the
                # two are independent POST-body fields, mutually exclusive only by UI convention), so
                # when the `kind == "value"` branch above did not fire (no `human_field`) this guard
                # keeps a supplied value from being silently dropped in favor of a manual marker.
                # Mask the bypass text the same way `reason` is masked (BE-0120): it is free-form
                # agent-authored prose, so it could echo a declared secret literal into the recorded
                # `manual.bypass` value and the narration below.
                bypass = (
                    _mask_secrets(proposal.human_bypass, secret_tokens or [])[0]
                    if proposal.human_bypass
                    else None
                )
                manual_step, todo = _manual_takeover_step(reason, bypass)
                steps.append(manual_step)
                say(f"[{len(steps)}] ✋ recorded human takeover as {describe_step(manual_step)}")
                say(f"[{len(steps)}] 📝 {todo}")
                continue
            say(f"[{n}] ✋ handoff resolved; re-observing the live screen")
            continue
        plan_tag = (
            f"(plan {proposal.plan_step}/{len(plan)}) " if proposal.plan_step and plan else ""
        )
        if len(proposal.steps) > 1:
            # Make the batch visible: one observation yielded several actions (BE-0178), so say so
            # rather than letting the steps appear one-by-one as if from separate model turns.
            say(
                f"[{n}] \U0001f4e6 batch — {len(proposal.steps)} actions from one observation; "
                "executing in order (aborts if the screen changes)"
            )
        # Execute the proposed steps as a batch (BE-0178). It is intra-screen by construction: after
        # each executed step the screen's identity is compared against the one the batch was planned
        # against, and the moment it changes with steps still pending, the rest is abandoned and the
        # loop re-observes — so a batch never acts on a screen that moved out from under it. Only the
        # steps that actually executed are recorded; the aborted tail is never written. The signature
        # ignores per-field state (fill/enabled/selected) so filling a form's fields — the batch's own
        # intended work — is not mistaken for a transition; only elements appearing/disappearing is.
        before_id = current_screen  # same `screen_identity(elements)` computed for the attach gate
        steps_before = len(steps)
        stop = rebatch = False
        for i, proposed in enumerate(proposal.steps):
            m = len(steps) + 1
            # One line per step: plan tag, the step's own intent (its `from_` reason; the turn-level
            # note is the fallback for the first action), and the concrete action. The reasoning is
            # masked first so the live stream never carries a secret literal (BE-0120).
            reason = proposed.from_ or (proposal.note if i == 0 else "")
            intent = _mask_secrets(reason, secret_tokens or [])[0] if reason else ""
            lead = f"[{m}] {plan_tag}\U0001f4ad {intent}  →  " if intent else f"[{m}] {plan_tag}→ "
            # Tokenize a matched secret before narrating or recording — so neither the written
            # scenario nor the live stream ever carries the literal (BE-0120) — but execute the
            # agent's unmodified step, since the app needs the real value to reach its screen.
            recorded_step, tokenized = _tokenize_secrets(proposed, secret_tokens or [])
            say(f"{lead}{describe_step(recorded_step)}")
            if tokenized:
                say(f"[{m}] \U0001f512 tokenized secret in typed text → {', '.join(tokenized)}")
            if not _execute_with_recovery(
                driver, proposed, clock, alert_guard, report=report, selection=selection
            ):
                # The step did not resolve, even after clearing prompts. If nothing in this turn
                # executed (the first proposed step did not resolve), offer a takeover; otherwise the
                # plan went stale mid-batch — abort the rest and re-observe next turn.
                if i == 0:
                    # The takeover trigger (BE-0185 box 1): the agent's target will not resolve to a
                    # unique element — the motivating case ("could not resolve that target"). Rather
                    # than abandon the recording, offer a takeover when a responder is present. The
                    # loop (not an LLM) raises this and never guesses which element to act on; the
                    # human operates the device and resumes, and a `manual` marker of the transition
                    # is recorded (no proposed bypass → unreproducible, so it fails loudly at run time
                    # until a bypass is wired, BE-0035 / BE-0052). Mask the reason (BE-0120) before it
                    # travels to the responder, the stream, or the recorded label. Without a responder,
                    # keep the clean, labeled stop the non-interactive / CI path relies on.
                    takeover_reason = _mask_secrets(
                        f"the agent could not resolve the target for: {describe_step(recorded_step)}",
                        secret_tokens or [],
                    )[0]
                    takeover = (
                        _offer_takeover(
                            handoff,
                            reason=takeover_reason,
                            elements=elements,
                            screenshot=screenshot,
                            bypass=None,
                        )
                        if handoff is not None
                        else None
                    )
                    if isinstance(takeover, tuple):
                        manual_step, todo = takeover
                        steps.append(manual_step)
                        say(
                            f"[{len(steps)}] ✋ recorded human takeover as "
                            f"{describe_step(manual_step)}"
                        )
                        say(f"[{len(steps)}] 📝 {todo}")
                        rebatch = True  # re-observe from the screen the human left
                        break
                    if takeover == "value_dropped":
                        say(
                            f"[{m}] ! a value was supplied but this takeover has no field to "
                            "record it into; stopping"
                        )
                    elif handoff is not None:
                        # a responder was present, so a takeover was offered but not completed (a
                        # cancel, a bare dismiss, or a timeout) — distinct from having no responder
                        say(f"[{m}] ! takeover not completed (declined or timed out); stopping")
                    else:
                        say(f"[{m}] ! could not resolve that target on the live screen; stopping")
                    stop = True
                else:
                    say(f"[{m}] ! a batched step no longer resolves; re-observing the new screen")
                    rebatch = True
                break
            steps.append(recorded_step)
            if _is_looping([describe_step(s) for s in steps]):
                say(
                    f"[{m}] ⟳ the agent is repeating actions without progress; stopping "
                    "(refine the goal, or the app may need accessibility ids for this control)"
                )
                stop = True
                break
            # A change on the last step (e.g. the submit tap) is legitimate — there is nothing left
            # to invalidate — so only abort when steps remain (Decision 2, "仕切り直し").
            if i < len(proposal.steps) - 1 and screen_identity(driver.query()) != before_id:
                say(f"[{m}] ↻ the screen changed mid-batch; re-observing before the next action")
                rebatch = True
                break
        if plan and proposal.plan_step:
            # Advance the "next" cursor past every step this turn actually executed. The model shares
            # one plan_step across a whole batch, so a batch of K actions plausibly covered K
            # consecutive plan steps from the labelled one — pointing the next hint only past the
            # single labelled step would leave it naming work the batch already did.
            executed = len(steps) - steps_before
            plan_cursor = max(plan_cursor, proposal.plan_step + max(0, executed - 1))
        if stop:
            break
        if rebatch:
            continue  # the batch was cut short; re-observe and re-plan from the new screen
        if proposal.done:
            # `finish` after the batch: the actions have run, now conclude with the goal's checks
            # (Decision 3). Its assertions verify the settled final screen exactly as before.
            note = _mask_secrets(proposal.note, secret_tokens or [])[0] if proposal.note else ""
            fin = (
                f"[{len(steps) + 1}] {plan_tag}\U0001f4ad {note}  →  "
                if note
                else f"[{len(steps) + 1}] {plan_tag}→ "
            )
            say(f"{fin}✓ finish · {len(proposal.expect)} assertion(s)")
            expect = proposal.expect
            settle = settle_step(expect)
            if settle is not None:
                steps.append(settle)  # let an async screen render before replay verifies
            break
        if not proposal.steps:
            say(f"[{n}] agent proposed no action; stopping")
            break

    # Report wall-clock duration on every exit path (finish, stop, max_steps) so the console — and
    # the serve progress pane, which both stream `say` — always show how long authoring took.
    say(
        f"⏱  record finished in {_format_elapsed(time.monotonic() - started)} · {len(steps)} step(s)"
    )
    if capture_video and steps:
        # A single step's inline `capture` starts the scenario-wide interval (requested_intervals),
        # so tag the first step — the whole replay is then recorded, not just one action's window.
        first = steps[0]
        kinds = list(first.capture or [])
        if "video" not in kinds:
            steps[0] = first.model_copy(update={"capture": [*kinds, "video"]})
    scenario = Scenario(name=name, steps=steps, expect=expect)
    # The goal is the scenario-level provenance (BE-0044): the natural language this whole scenario
    # was authored from. Set by attribute since the field's `from` alias is a Python keyword.
    scenario.from_ = goal
    return scenario

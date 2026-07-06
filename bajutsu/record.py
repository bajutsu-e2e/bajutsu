"""Record loop (Tier 1) — drive an app with an agent and emit a scenario.

observe (query) -> agent proposes the next action -> execute it -> repeat, until
the agent signals done or max_steps is hit. The recorded steps form a deterministic
scenario that `run` later replays with no AI.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from bajutsu import usage as _usage
from bajutsu.agent import Agent, Observation
from bajutsu.drivers import base
from bajutsu.elements import shows_app_ui
from bajutsu.orchestrator import BlockedHandler, Clock, RealClock, _action_of, _do_action, _wait
from bajutsu.scenario import Assertion, Scenario, Selector, Step

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
        return f"#{sel.id}"
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


def _describe_step(step: Step) -> str:
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


def _screenshot_bytes(driver: base.Driver) -> bytes | None:
    """Capture a PNG of the current screen as bytes (best-effort).

    Returns None on both a genuinely empty capture and a failure — callers treat the
    screenshot as optional and continue either way — but logs a warning when the capture
    *fails* (a stale simulator, a permissions error, a full disk), so a real failure stays
    distinguishable from "there was nothing to capture" instead of vanishing into None.
    """
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        driver.screenshot(path)
        return Path(path).read_bytes() or None
    except Exception as exc:
        _logger.warning("screenshot capture failed: %s", exc, exc_info=True)
        return None
    finally:
        # Clean up on both paths: on a capture failure the temp file is already created
        # (delete=False), so without this a repeated failure leaks PNGs into the temp dir.
        if path is not None:
            Path(path).unlink(missing_ok=True)


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


def _settle_step(expect: list[Assertion], timeout: float = 5.0) -> Step | None:
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


def _execute(driver: base.Driver, step: Step, clock: Clock) -> None:
    kind = _action_of(step)
    if kind == "wait":
        assert step.wait is not None
        _wait(driver, step.wait, clock)
    elif kind == "assert_":
        return  # assertions are checks, not actions to perform while recording
    else:
        _do_action(driver, step)


def _clear_blocking(
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
) -> bool:
    """Execute a step; if it fails because a prompt is covering the app, clear it and retry."""
    try:
        _execute(driver, step, clock)
        return True
    except base.SelectorError:
        if guard is None:
            return False
        (report or (lambda _m: None))(
            "⚠️  a step could not act — a system prompt may be covering the app; recovering …"
        )
        _clear_blocking(driver, guard, clock, report=report)
        try:
            _execute(driver, step, clock)
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
    report: Reporter | None = None,
) -> Scenario:
    """Explore toward `goal` with `agent`, returning the recorded scenario.

    If `alert_guard` is given, an unexpected OS prompt (e.g. iOS "Save Password?")
    that surfaces while authoring is dismissed so the agent keeps a clean view. The
    dismissal is environmental, not a recorded step; replay handles it with
    `run --dismiss-alerts`.

    If `secret_tokens` is given (`(literal value, "${secrets.NAME}")` pairs, longest-value-first),
    a typed value matching a declared secret is recorded as its `${secrets.X}` token, never the
    literal (BE-0120); the app is still driven with the real value so the authenticated screen is
    reached. Empty/None records every value verbatim.

    If `report` is given, each turn's decision (the agent's proposed action and reason)
    is streamed to it as a one-line string, so a caller can show progress live.
    """
    clock = clock or RealClock()
    say = report or (lambda _msg: None)
    started = time.monotonic()  # wall-clock: how long the author actually waited (model + device)
    steps: list[Step] = []
    expect: list[Assertion] = []
    plan = _plan_goal(agent, goal, say)

    for _ in range(max_steps):
        if alert_guard is not None:
            _clear_blocking(driver, alert_guard, clock, report=report)
        elements = driver.query()
        if alert_guard is not None and not shows_app_ui(elements):
            # A prompt slipped in after the last clear: don't ask the agent to act on a
            # dead screen (it would hallucinate ids); re-clear on the next iteration.
            clock.sleep(0.3)
            continue
        n = len(steps) + 1
        say(f"[{n}] observing {len(elements)} elements; asking the agent (waits on the model) …")
        screenshot = _screenshot_bytes(driver) if with_screenshot else None
        before = _usage.snapshot()
        proposal = agent.next_action(
            Observation(
                goal=goal,
                screen=elements,
                history=list(steps),
                screenshot=screenshot,
                plan=plan,
            )
        )
        spent = (
            _usage.snapshot() - before
        )  # single-threaded record loop → this turn's tokens exactly
        if spent.total_tokens:
            say(f"[{n}] \U0001f916 agent replied · {spent.total_tokens:,} tokens")
        # One line per step: which plan step it advances, the intent (what it is trying to do), and
        # the concrete action, together. The reasoning is masked first so the live stream never
        # carries a secret literal (BE-0120).
        intent = _mask_secrets(proposal.note, secret_tokens or [])[0] if proposal.note else ""
        plan_tag = (
            f"(plan {proposal.plan_step}/{len(plan)}) " if proposal.plan_step and plan else ""
        )
        lead = f"[{n}] {plan_tag}\U0001f4ad {intent}  →  " if intent else f"[{n}] {plan_tag}→ "
        if proposal.done:
            say(f"{lead}✓ finish · {len(proposal.expect)} assertion(s)")
            expect = proposal.expect
            settle = _settle_step(expect)
            if settle is not None:
                steps.append(settle)  # let an async screen render before replay verifies
            break
        if proposal.step is None:
            say(f"[{n}] agent proposed no action; stopping")
            break
        # Tokenize a matched secret before narrating or recording — so neither the written
        # scenario nor the live progress stream ever carries the literal (BE-0120) — but execute
        # the agent's unmodified proposal, since the app needs the real value to reach its screen.
        recorded_step, tokenized = _tokenize_secrets(proposal.step, secret_tokens or [])
        say(f"{lead}{_describe_step(recorded_step)}")
        if tokenized:
            say(f"[{n}] \U0001f512 tokenized secret in typed text → {', '.join(tokenized)}")
        if not _execute_with_recovery(driver, proposal.step, clock, alert_guard, report=report):
            say(f"[{n}] ! could not resolve that target on the live screen; stopping")
            break  # the proposed action did not resolve, even after clearing prompts
        steps.append(recorded_step)
        if _is_looping([_describe_step(s) for s in steps]):
            say(
                f"[{n}] ⟳ the agent is repeating actions without progress; stopping "
                "(refine the goal, or the app may need accessibility ids for this control)"
            )
            break

    # Report wall-clock duration on every exit path (finish, stop, max_steps) so the console — and
    # the serve progress pane, which both stream `say` — always show how long authoring took.
    say(
        f"⏱  record finished in {_format_elapsed(time.monotonic() - started)} · {len(steps)} step(s)"
    )
    scenario = Scenario(name=name, steps=steps, expect=expect)
    # The goal is the scenario-level provenance (BE-0044): the natural language this whole scenario
    # was authored from. Set by attribute since the field's `from` alias is a Python keyword.
    scenario.from_ = goal
    return scenario

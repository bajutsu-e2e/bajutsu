"""The deterministic Tier-2 run loop: act -> (wait) -> verify, per step.

Pass/fail comes from machine assertions only; no AI is involved. Execution stops at the first
failure. Backend-agnostic via base.Driver (real driver or FakeDriver); evidence, relaunch, and
device control are injected by the runner.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial

from bajutsu import assertions, interp
from bajutsu.assertions import AssertionResult, EvalContext
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink, NullSink, intervals
from bajutsu.mailbox import extract_value, select
from bajutsu.orchestrator.actions import _action_of, _do_action, _step_label
from bajutsu.orchestrator.evidence_rules import (
    _collect_captures,
    _extract_stable_key,
    _kind_of,
    _run_extract,
    requested_intervals,
)
from bajutsu.orchestrator.substitution import _interp_asserts, _interp_step
from bajutsu.orchestrator.types import (
    AlertEvent,
    BlockedHandler,
    Clock,
    DeviceControl,
    MailboxReader,
    NetworkSource,
    ProgressFn,
    RealClock,
    RelaunchFn,
    RunResult,
    SelectionState,
    StepOutcome,
    _no_network,
    scenario_slug,
)
from bajutsu.orchestrator.waits import (
    WaitTick,
    WaitTrace,
    _adaptive_sleep,
    _timeout_floor,
    _wait,
    describe_wait,
)
from bajutsu.scenario import Assertion, Email, Extract, ForEach, If, Scenario, Selector, Step
from bajutsu.webview import DomSource, WebContextDriver

_logger = logging.getLogger(__name__)

# How often `email` re-polls the mailbox. Unlike the UI's 50 ms `_POLL`, each tick is a remote HTTP
# request to a (often rate-limited / metered) provider, so it polls about once a second.
_EMAIL_POLL = 1.0


def _fail_reason(results: list[AssertionResult]) -> str:
    return "; ".join(r.reason for r in results if not r.ok)


# Assertion kinds whose result a tree re-read cannot change: the clipboard and screenshot are read
# once, and the network kinds have their own `wait until: request`. Waiting on one of these would
# only idle to the deadline, so the poll stops as soon as every *still-failing* assertion is one of
# them. Any other (tree-derived) kind — value / label / exists / count / state / golden, and any
# future UI kind — keeps the wait, which is the read-race this poll exists to close.
_READ_ONCE_KINDS = frozenset(
    {"clipboard", "visual", "request", "responseSchema", "requestSequence", "event"}
)


def _poll_asserts(
    driver: base.Driver,
    asserts: list[Assertion],
    network: NetworkSource,
    clock: Clock,
    *,
    ctx: EvalContext,
) -> tuple[list[AssertionResult], list[base.Element]]:
    """Evaluate `asserts` as a condition wait: re-read the tree until it passes or the deadline.

    Polling `query()` until `passed()` — bounded by a wall-clock deadline, never a fixed sleep — is
    what keeps a fast read (the resident channel, ~0.1s) no more flaky than a slow one (`uiautomator
    dump`, ~2.4s, which incidentally waited out an action's async-mirrored value: a value an action
    mirrors into the tree can land a beat after the action returns, as Compose recomposes the
    `content-desc` asynchronously). The wait budget is the lane's wait floor
    (`BAJUTSU_MIN_WAIT_TIMEOUT`), the same knob every other condition wait honors — so it is zero
    (a single read, today's behavior) on lanes that don't set it, and the Android e2e lane's 15s
    where the race lives. Only the UI tree goes stale, so only it is re-read; the caller takes the
    screenshot and reads the clipboard once, and the poll ends the moment nothing a tree re-read
    could fix is still failing (`_READ_ONCE_KINDS`).

    Returns the final results and the last tree read, so a step-level caller can reuse that settled
    tree as its `after` snapshot instead of re-querying (BE-0299 Unit 1 / BE-0259).
    """
    deadline = clock.now() + _timeout_floor()
    while True:
        t0 = clock.now()
        tree = driver.query()
        results = assertions.evaluate(tree, asserts, network(), ctx=ctx)
        if assertions.passed(results) or clock.now() >= deadline:
            return results, tree
        if all(r.ok or r.kind in _READ_ONCE_KINDS for r in results):
            return results, tree  # only read-once assertions are left failing; a re-read can't help
        _adaptive_sleep(clock, t0)


def _evaluate_expect(
    driver: base.Driver,
    expect: list[Assertion],
    network: NetworkSource,
    clock: Clock,
    *,
    ctx: EvalContext,
) -> list[AssertionResult]:
    """Evaluate the trailing `expect` block as a condition wait (BE-0245), via `_poll_asserts`.

    The scenario-level `expect` needs only the assertion results, not the settled tree, so it drops
    the tree `_poll_asserts` also returns.
    """
    results, _ = _poll_asserts(driver, expect, network, clock, ctx=ctx)
    return results


def _settle_extract_read(
    driver: base.Driver,
    extracts: Mapping[str, Extract],
    clock: Clock,
    *,
    initial: list[base.Element] | None = None,
) -> list[base.Element]:
    """Read the post-step tree, polling until the properties `extract` reads stop changing.

    An `extract` has no assertion to satisfy — it copies a value out — so this is the settle-shaped
    sibling of `_poll_asserts`: it stops when two consecutive reads share the extract projection
    (`_extract_stable_key`), or the same wall-clock deadline (`BAJUTSU_MIN_WAIT_TIMEOUT`) elapses.
    With no wait floor the budget is zero, so it reads exactly once — today's single-read behavior on
    every lane that does not set the floor (BE-0299 Unit 3).

    `initial`, when given, is the seed a non-mutating step (`assert` / `wait`) already settled on: it
    is taken as the first sample so the poll refines that seed in place rather than re-reading it,
    which is why this is applied at that earlier read site — the seed short-circuits `_ScreenRead`, so
    the poll cannot live there for a seeded step. A mutating step passes no seed and reads fresh.
    """
    deadline = clock.now() + _timeout_floor()
    tree = initial if initial is not None else driver.query()
    key = _extract_stable_key(tree, extracts)
    while clock.now() < deadline:
        t0 = clock.now()
        next_tree = driver.query()
        next_key = _extract_stable_key(next_tree, extracts)
        if next_key == key:
            return next_tree
        tree, key = next_tree, next_key
        _adaptive_sleep(clock, t0)
    # Deadline hit while the extract projection was still moving: return the latest read (best-effort,
    # like the driver `_settle`), and say so, so a later assert failing on a still-propagating value is
    # traceable to an un-settled extract rather than looking inexplicable (BE-0299 Unit 3).
    _logger.debug(
        "extract settle: projection still changing at the wait deadline; using latest read"
    )
    return tree


class _ScreenRead:
    """A step's post-step screen read, taken at most once and cached (BE-0234 Unit 2).

    On the adb backend a screen read (`uiautomator dump`) is the dominant per-step cost — ~2.4s
    against ~0.1-0.3s for a lighter read channel — so the end-of-step read is deferred until a
    consumer actually needs it: a `screenChanged` capture, an `extract`, or a `wait`-timeout
    diagnostic. A plain `tap`/`assert` step with none of these under a `NullSink` never reads.
    When it is read, the tree also seeds the next step's `before` — nothing actuates between a
    step's `after` and the next step's `before`, so they observe identical device state.

    A non-mutating step (`assert`, `wait`) already queried the tree to evaluate itself, and nothing
    actuates between that query and this read, so the caller can `seed` it with that snapshot: a
    consumer then reuses it instead of issuing a second identical query (BE-0259). A seeded read is
    not a runner-issued read — `queried` stays False — so the BE-0234 read-count yardstick keeps
    counting only the queries this class actually performs.

    `read` overrides the default `query()` for the first, uncached read: a step whose `extract` will
    consume the tree passes a property-aware settle poll here (`_settle_extract_read`), so the value
    it copies out is a settled one rather than whichever was still propagating when a single read
    fired (BE-0299 Unit 3). It is mutually exclusive with `seed` — a seeded step is refined at its
    earlier read site instead — and only fires on a genuine read, so `queried` still reflects one.
    """

    def __init__(
        self,
        driver: base.Driver,
        seed: list[base.Element] | None = None,
        *,
        read: Callable[[], list[base.Element]] | None = None,
    ) -> None:
        # A seed short-circuits `.get()`, so a `read` passed alongside one would be silently dropped —
        # fail loudly instead (the two are mutually exclusive by construction; see the class docstring).
        assert not (seed is not None and read is not None), "seed and read are mutually exclusive"
        self._driver = driver
        self._tree = seed
        self._available = seed is not None
        self._queried = False
        self._read = read

    def get(self) -> list[base.Element]:
        """The post-step tree: the seed if one was given, else read once (via `read`) and cached."""
        if not self._available:
            self._tree = self._read() if self._read is not None else self._driver.query()
            self._available = True
            self._queried = True
        assert (
            self._tree is not None
        )  # set on seed or the read above; narrows the Optional for mypy
        return self._tree

    @property
    def cached(self) -> list[base.Element] | None:
        """The tree if seeded or already read, else None — so a capture can read lazily on its own."""
        return self._tree if self._available else None

    @property
    def queried(self) -> bool:
        """Whether `get()` issued a `query()` — False for a seeded (reused) tree."""
        return self._queried


def _clipboard_for(block: list[Assertion], control: DeviceControl | None) -> str | None:
    """The device pasteboard, read once when `block` has a `clipboard` assertion; None otherwise.

    None when no `clipboard` assertion is present, when no device-control channel is available
    (fake driver / parallel run), or when the read itself fails (`simctl pbpaste` errored). In every
    None case a `clipboard` assertion fails cleanly via `evaluate` rather than aborting the run —
    the read is a verification input, not a scenario step."""
    if control is None or not any(a.clipboard is not None for a in block):
        return None
    try:
        return control.get_clipboard()
    except (OSError, subprocess.CalledProcessError):
        return None


def _do_email(
    email: Email,
    clock: Clock,
    mailbox: MailboxReader | None,
    bindings: dict[str, str] | None,
) -> tuple[bool, str]:
    """Poll the mailbox until a matching message arrives, then extract its value into `vars.*`.

    A condition wait bounded by `email.timeout` (never a fixed sleep): it baselines the ids present
    at the start so only mail arriving *after* counts (skew-free), then re-fetches until a match or
    the deadline. A missing mailbox, a timeout, or a matched message whose body the regex can't hit
    is a clean failure — never a silent wrong value. `mailbox.fetch` raising `SelectorError` (an
    unreachable / non-2xx endpoint) propagates to the caller's handler, which records it as a failure.
    """
    if mailbox is None:
        return False, "email: no mailbox configured (set targets.<name>.mailbox)"
    if bindings is None:  # defensive: the run loop always passes a dict for a step
        return True, ""
    deadline = clock.now() + email.timeout
    baseline = frozenset(m.id for m in mailbox.fetch(email.timeout))
    while True:
        remaining = deadline - clock.now()
        if remaining <= 0:
            return False, f"email: no matching message within {email.timeout:g}s"
        # Bound each fetch by the time left, so a single hung request can't overrun email.timeout.
        picked = select(mailbox.fetch(remaining), email.match, baseline)
        if picked is not None:
            value = extract_value(picked.body, email.extract)
            if value is None:
                return False, "email: matched a message but extract regex did not match its body"
            bindings[f"vars.{email.extract.var}"] = value
            return True, ""
        clock.sleep(min(_EMAIL_POLL, deadline - clock.now()))


def _run_step_body(
    driver: base.Driver,
    step: Step,
    kind: str,
    clock: Clock,
    network: NetworkSource,
    relaunch: RelaunchFn | None = None,
    bindings: dict[str, str] | None = None,
    control: DeviceControl | None = None,
    mailbox: MailboxReader | None = None,
    ctx: EvalContext | None = None,
    wait_trace: WaitTrace | None = None,
    selection: SelectionState | None = None,
    on_blocked: BlockedHandler | None = None,
    alerts: list[AlertEvent] | None = None,
    on_wait_tick: WaitTick | None = None,
) -> tuple[bool, str, list[AssertionResult], list[base.Element] | None]:
    """Execute one step's effect, returning (ok, reason, assertion_results, snapshot).

    ``snapshot`` is the settled tree a non-mutating step (`assert`, `wait`) already queried to
    evaluate itself; the caller reuses it as the step's `after` instead of re-querying (BE-0259). It
    is ``None`` for steps that mutate the screen (`tap`, `type`, …) or read no tree (`email`,
    `wait until: request`), so the post-step read falls back to a fresh query for exactly the steps
    where "before" and "after" may differ.

    The caller is responsible for interpolation (``_interp_step``) before
    calling this function. ``wait_trace``, when given for a wait step, records the poll timeline so a
    timeout is diagnosable from artifacts (BE-0231 Unit 1). ``on_blocked``/``alerts``, when given for
    a wait step, are passed through to ``_wait``'s mid-wait alert guard (BE-0269); other step kinds
    ignore them."""
    try:
        if kind == "wait":
            assert step.wait is not None
            ok, reason, tree = _wait(
                driver,
                step.wait,
                clock,
                network,
                trace=wait_trace,
                on_blocked=on_blocked,
                alerts=alerts,
                on_tick=on_wait_tick,
            )
            return ok, reason, [], tree
        if kind == "email":
            assert step.email is not None
            ok, reason = _do_email(step.email, clock, mailbox, bindings)
            return ok, reason, [], None
        if kind == "assert_":
            assert step.assert_ is not None
            clip = _clipboard_for(step.assert_, control)
            # A step-level assert sees only golden + clipboard: no per-step screenshot is taken, so
            # `visual` / `responseSchema` have no fresh input here (they run at scenario `expect`).
            # Drop them from the bundled context to preserve that behavior (BE-0250 Unit 2).
            step_ctx = replace(ctx or EvalContext(), visual=None, schema=None, clipboard=clip)
            # A condition wait, not a single snapshot: a value the prior action mirrors into the tree
            # a beat late is caught, the same race the trailing `expect` already closes (BE-0299
            # Unit 2). Zero-budget (no wait floor) reads exactly once, as before.
            results, tree = _poll_asserts(driver, step.assert_, network, clock, ctx=step_ctx)
            ok = assertions.passed(results)
            return ok, "" if ok else _fail_reason(results), results, tree
        _do_action(driver, step, relaunch, control, bindings, selection)
        return True, "", [], None
    except (base.SelectorError, base.UnsupportedAction, NotImplementedError) as e:
        return False, str(e), [], None


def run_scenario(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock | None = None,
    sink: EvidenceSink | None = None,
    on_blocked: BlockedHandler | None = None,
    scenario_id: str | None = None,
    network: NetworkSource = _no_network,
    relaunch: RelaunchFn | None = None,
    bindings: Mapping[str, str] | None = None,
    control: DeviceControl | None = None,
    progress: ProgressFn | None = None,
    ctx: EvalContext | None = None,
    mailbox: MailboxReader | None = None,
    webview_bridge: DomSource | None = None,
) -> RunResult:
    """Run one scenario deterministically, firing capturePolicy rules into `sink`.

    Heavy scenario-wide intervals (video / deviceLog / appTrace) are opt-in (BE-0028): the sink
    starts only the interval kinds the scenario actually requests (`requested_intervals`) before
    the first step and finalizes them after verification, attaching them to the result. A scenario
    that requests none records no intervals; the instant baseline still fires every step.

    If a step fails and `on_blocked` clears a blocking condition (e.g. dismisses a
    system alert), the step is retried once before being recorded as a failure.
    """
    clock = clock or RealClock()
    sink = sink or NullSink()
    ctx = ctx or EvalContext()
    sid = scenario_id or scenario_slug(scenario.name)
    recordings = sink.start_scenario_intervals(sid, requested_intervals(scenario))
    wants_screen_changed = any(r.on.event == "screenChanged" for r in scenario.capture_policy)
    outcomes: list[StepOutcome] = []
    expect_results: list[AssertionResult] = []
    expect_alerts: list[AlertEvent] = []
    failure: str | None = None
    artifacts: list[Artifact] = []
    scenario_start = clock.now()  # ~video start; step offsets are measured from here
    # Mutable bindings: extract steps populate vars.* during the run; scenario-level
    # expect sees the accumulated values.
    live_bindings: dict[str, str] = dict(bindings or {})

    try:
        failure = _run_steps(
            driver,
            scenario,
            clock,
            sink,
            on_blocked,
            wants_screen_changed,
            outcomes,
            scenario_start,
            sid,
            network,
            relaunch,
            live_bindings,
            control,
            progress,
            mailbox,
            ctx,
            webview_bridge,
        )
        if failure is None and scenario.expect:
            expect = _interp_asserts(scenario.expect, live_bindings)
            clip = _clipboard_for(expect, control)
            if ctx.visual is not None:
                driver.screenshot(str(ctx.visual.screenshot_path))
            expect_results = _evaluate_expect(
                driver, expect, network, clock, ctx=replace(ctx, clipboard=clip)
            )
            if not assertions.passed(expect_results) and on_blocked is not None:
                event = on_blocked(driver)
                if event is not None:
                    expect_alerts.append(event)
                    if ctx.visual is not None:
                        driver.screenshot(str(ctx.visual.screenshot_path))
                    # Re-read the clipboard too: clearing the block may have let the app update the
                    # pasteboard, so the retry must compare against the fresh value, not the stale one.
                    clip = _clipboard_for(expect, control)
                    expect_results = _evaluate_expect(
                        driver, expect, network, clock, ctx=replace(ctx, clipboard=clip)
                    )  # retry once
            if not assertions.passed(expect_results):
                failure = "expect: " + _fail_reason(expect_results)
    finally:
        artifacts = sink.finish_scenario_intervals(sid, recordings)

    return RunResult(
        scenario=scenario.name,
        ok=failure is None,
        steps=outcomes,
        expect_results=expect_results,
        failure=failure,
        artifacts=artifacts,
        backend=getattr(driver, "name", ""),
        duration_s=max(0.0, clock.now() - scenario_start),
        expect_alerts=expect_alerts,
    )


# A recursive step runner: run these steps against this active driver, return the failure or None.
# The driver is passed explicitly so a web block can hand its inner steps a WebView driver without
# any shared mutable state (BE-0172).
_ExecSteps = Callable[[list[Step], base.Driver], str | None]


class _StepCounter:
    """A monotonically increasing step index shared across the recursive step loop (BE-0172).

    A named replacement for the former ``step_counter = [0]`` closure smuggle: ``take()`` returns
    the current index and advances, so nested ``for_each`` / ``web`` groups keep unique, ordered
    indices without a boxed list.
    """

    def __init__(self) -> None:
        self._next = 0

    def take(self) -> int:
        idx = self._next
        self._next += 1
        return idx


def _run_if(
    driver: base.Driver,
    if_block: If,
    clock: Clock,
    network: NetworkSource,
    bindings: dict[str, str],
    exec_steps: _ExecSteps,
) -> tuple[bool, str]:
    """Evaluate the condition (with interpolation) and run the matching branch."""
    interp_condition = _interp_asserts([if_block.condition], bindings)[0]
    elements = driver.query()
    results = assertions.evaluate(elements, [interp_condition], network())
    branch = if_block.then if assertions.passed(results) else (if_block.else_ or [])
    if not branch:
        return True, ""
    failure = exec_steps(branch, driver)
    return (True, "") if failure is None else (False, failure)


def _run_for_each(
    driver: base.Driver,
    loop: ForEach,
    bindings: dict[str, str],
    exec_steps: _ExecSteps,
) -> tuple[bool, str]:
    """Iterate over elements matching the (interpolated) selector."""
    sel_dict = interp.interpolate(loop.sel.model_dump(by_alias=True), bindings)
    sel = Selector.model_validate(sel_dict).as_selector()
    elements = driver.query()
    matched = base.find_all(elements, sel)
    for el in matched:
        ident = el.get("identifier")
        if not ident:
            return False, f"forEach: matched element has no identifier (label={el.get('label')!r})"
        bindings[f"vars.{loop.as_}"] = ident
        failure = exec_steps(loop.steps, driver)
        if failure is not None:
            return False, failure
    return True, ""


def _run_steps(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock,
    sink: EvidenceSink,
    on_blocked: BlockedHandler | None,
    wants_screen_changed: bool,
    outcomes: list[StepOutcome],
    scenario_start: float,
    sid: str,
    network: NetworkSource,
    relaunch: RelaunchFn | None = None,
    bindings: dict[str, str] | None = None,
    control: DeviceControl | None = None,
    progress: ProgressFn | None = None,
    mailbox: MailboxReader | None = None,
    ctx: EvalContext | None = None,
    webview_bridge: DomSource | None = None,
) -> str | None:
    """Run the step loop, appending outcomes; return the failure string or None.

    ``bindings`` is a mutable dict (guaranteed by ``run_scenario``) — extract
    steps add ``vars.*`` entries so that subsequent steps and scenario-level
    ``expect`` can reference them."""
    assert bindings is not None
    counter = _StepCounter()
    # One selection tracker per run, shared across the recursive step loop (like `_StepCounter`), so
    # a `copy` sees the selection a prior `select` left — and any action in between clears it (BE-0265).
    selection = SelectionState()
    # `prev_after` carries a step's post-step tree to the next step's `before` (BE-0234 Unit 2):
    # nothing actuates between the two, so they observe the same device state and the `before` read
    # is skipped. It holds only a tree we actually read; a step that took no read leaves it None so
    # the next `before` reads fresh, and a `web` block resets it (the tree is a different driver's).
    prev_after: list[base.Element] | None = None
    total_reads = 0  # runner-issued screen reads, the BE-0234 read-count yardstick (Unit 1)

    def exec_steps(steps: list[Step], active_driver: base.Driver) -> str | None:
        nonlocal prev_after, total_reads
        for step in steps:
            kind = _action_of(step)
            idx = counter.take()
            outcome = StepOutcome(index=idx, action=kind)
            if progress is not None:
                progress(f"{sid} · step {idx + 1}: {_step_label(step, kind)}")
            step_id = f"{sid}/{step.name or f'step{idx}'}"
            start = clock.now()
            outcome.started_at = max(0.0, start - scenario_start)

            if kind == "if_":
                assert step.if_ is not None
                ok, reason = _run_if(active_driver, step.if_, clock, network, bindings, exec_steps)
                outcome.ok, outcome.reason = ok, reason
                outcome.duration_s = clock.now() - start
                outcomes.append(outcome)
                if not ok:
                    return f"step {idx} ({kind}): {reason}"
                continue

            if kind == "for_each":
                assert step.for_each is not None
                ok, reason = _run_for_each(active_driver, step.for_each, bindings, exec_steps)
                outcome.ok, outcome.reason = ok, reason
                outcome.duration_s = clock.now() - start
                outcomes.append(outcome)
                if not ok:
                    return f"step {idx} ({kind}): {reason}"
                continue

            if kind == "web":
                assert step.web is not None
                try:
                    if webview_bridge is None:
                        ok, reason = (
                            False,
                            "web: no WebView bridge configured (BAJUTSU_WEBVIEW_PORT not set)",
                        )
                    else:
                        sel = interp.interpolate(
                            step.web.within.model_dump(by_alias=True), bindings
                        )
                        host_sel = Selector.model_validate(sel).as_selector()
                        base.resolve_unique(active_driver.query(), host_sel)
                        host_id = step.web.within.first_id()
                        if host_id is None:
                            ok, reason = False, "web: within selector must specify an id"
                        else:
                            # The inner steps run against a WebView driver; the active driver is
                            # passed explicitly, so control returns to `active_driver` for the
                            # steps after this block with no shared mutable state (BE-0172).
                            web_driver = WebContextDriver(bridge=webview_bridge, webview_id=host_id)
                            # The inner steps run on a different driver, so its trees must not seed a
                            # native step's `before`: reset around the block on both sides (BE-0234).
                            prev_after = None
                            failure = exec_steps(step.web.steps, web_driver)
                            prev_after = None
                            ok = failure is None
                            reason = failure or ""
                except base.SelectorError as e:
                    ok, reason = False, str(e)
                outcome.ok, outcome.reason = ok, reason
                outcome.duration_s = clock.now() - start
                outcomes.append(outcome)
                if not ok:
                    return f"step {idx} ({kind}): {reason}"
                continue

            interp_step = _interp_step(step, bindings)
            # `before` is needed only for a `screenChanged` policy. Reuse the previous step's
            # post-step tree when we have one (same device state — nothing actuated in between), so
            # the read drops to (near) zero across the scenario; only the first step, or a step after
            # one that took no read, reads a fresh `before` (BE-0234 Unit 2).
            if not wants_screen_changed:
                before = None
            elif prev_after is not None:
                before = prev_after
            else:
                before = active_driver.query()
                total_reads += 1
            # A `for` wait records its poll timeline so a timeout is diagnosable from artifacts
            # (BE-0231 Unit 1); the on_blocked retry gets a fresh trace so the diagnostic reflects the
            # attempt that actually failed.
            wait_trace = WaitTrace() if kind == "wait" and interp_step.wait is not None else None
            # A wait blocks silently for its whole timeout; stream a "still waiting <condition>" line
            # so the run log shows what it is blocked on, live. Only when progress is wired.
            wait_tick: WaitTick | None = None
            if progress is not None and kind == "wait" and interp_step.wait is not None:
                desc = describe_wait(interp_step.wait)
                prefix = f"{sid} · step {idx + 1}"

                def wait_tick(remaining: float, _desc: str = desc, _prefix: str = prefix) -> None:
                    assert progress is not None
                    progress(f"{_prefix}: waiting {_desc} ({remaining:.0f}s left)")

            ok, reason, results, snapshot = _run_step_body(
                active_driver,
                interp_step,
                kind,
                clock,
                network,
                relaunch,
                bindings,
                control,
                mailbox,
                ctx,
                wait_trace=wait_trace,
                selection=selection,
                on_blocked=on_blocked,
                alerts=outcome.alerts,
                on_wait_tick=wait_tick,
            )
            if not ok and on_blocked is not None:
                event = on_blocked(active_driver)
                if event is not None:
                    outcome.alerts.append(event)
                    wait_trace = WaitTrace() if wait_trace is not None else None
                    # The retry is the end-of-step "one more shot": it does not re-arm the mid-wait
                    # guard (no on_blocked passed), so a step's AI-vision calls stay bounded at
                    # _GUARD_MAX_ATTEMPTS (mid-wait) + 1 (this end-of-step dismiss).
                    ok, reason, results, snapshot = _run_step_body(
                        active_driver,
                        interp_step,
                        kind,
                        clock,
                        network,
                        relaunch,
                        bindings,
                        control,
                        mailbox,
                        ctx,
                        wait_trace=wait_trace,
                        selection=selection,
                        on_wait_tick=wait_tick,
                    )
            outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
            outcome.duration_s = clock.now() - start

            # The post-step read is lazy (BE-0234 Unit 2): `.get()` reads (once) only where a
            # consumer needs the tree, so a step with no consumer under a NullSink never reads. A
            # non-mutating step (`assert`/`wait`) hands back the tree it already settled on, so the
            # read reuses that snapshot rather than issuing a second identical query (BE-0259);
            # `snapshot` is None for mutating/tree-less steps, restoring the fresh post-step read.
            #
            # An `extract` on this step consumes the read, so it must observe a value that has stopped
            # propagating, not whichever one the single read caught (BE-0299 Unit 3). Gated on
            # `outcome.ok`, matching where the extract actually runs (below), so a failed step never
            # pays the poll for a value it will not read. A mutating step (or `wait until: request`,
            # which hands back no tree) has no seed, so the property-aware read is deferred into
            # `_ScreenRead` and fires only when a consumer needs the tree; `partial` binds this step's
            # driver/extracts now, not a later iteration's. A seeded non-mutating step cannot poll
            # there — the seed short-circuits `.get()` — so it is refined here, at that earlier read
            # site, before `_ScreenRead` reuses it (keeping `queried` False for it).
            read: Callable[[], list[base.Element]] | None = None
            if outcome.ok and interp_step.extract:
                if snapshot is None:
                    read = partial(_settle_extract_read, active_driver, interp_step.extract, clock)
                else:
                    snapshot = _settle_extract_read(
                        active_driver, interp_step.extract, clock, initial=snapshot
                    )
            screen = _ScreenRead(active_driver, seed=snapshot, read=read)
            screen_changed = before is not None and screen.get() != before

            # An unconditional first-wait diagnostic on a `for`-wait timeout: capturePolicy may not
            # request an element dump on failure, so without this the timeout leaves no evidence to
            # decide which cause fired (BE-0231 Unit 1). Deterministic, no LLM (prime directive 1).
            # `polls > 0` fires only after a `for`-wait ran (only that branch records the trace), so
            # the trigger is a structural fact, not the wording of the timeout message.
            if wait_trace is not None and not ok and wait_trace.polls > 0:
                try:
                    art = sink.wait_diagnostic(step_id, trace=wait_trace, elements=screen.get())
                except OSError as exc:
                    # Best-effort evidence: a disk/permission failure writing the diagnostic must not
                    # mask the real timeout with an I/O traceback — keep the timeout as the failure and
                    # disclose the lost evidence loudly. A genuine bug (e.g. a redaction error) still
                    # surfaces rather than being swallowed here.
                    _logger.warning("dropping wait-timeout diagnostic: write failed: %s", exc)
                else:
                    if art is not None:
                        outcome.artifacts.append(art)

            if outcome.ok and interp_step.extract:
                ext_ok, ext_reason = _run_extract(screen.get(), interp_step.extract, bindings)
                if not ext_ok:
                    outcome.ok, outcome.reason = False, ext_reason

            fired = _collect_captures(scenario, step, kind, outcome.ok, screen_changed)
            # Interval kinds are recorded scenario-wide (run_scenario), so only the
            # instant kinds are captured per step here. Pass the tree only if we already read it;
            # otherwise `elements=None` lets the sink's `elements` writer read on its own (a NullSink
            # reads nothing), so a FileSink run stays at one read and a NullSink run at zero. A `web`
            # block captures against the native `driver`, so it must read the active (web) tree here
            # rather than let the native writer fall back to a mismatched tree (BE-0234 Unit 2).
            instant = [t for t in fired if _kind_of(t) not in intervals.INTERVAL_KINDS]
            els = screen.get() if active_driver is not driver else screen.cached
            outcome.artifacts.extend(sink.capture(driver, step_id, instant, elements=els))
            if screen.queried:
                total_reads += 1
            # Seed the next step's `before` only with a tree we actually read; if we skipped the
            # read, the next `before` reads fresh (BE-0234 Unit 2).
            prev_after = screen.cached

            outcomes.append(outcome)
            if not outcome.ok:
                return f"step {idx} ({kind}): {outcome.reason}"
        return None

    result = exec_steps(scenario.steps, driver)
    _logger.debug("%s: %d runner-issued screen reads (BE-0234)", sid, total_reads)
    return result

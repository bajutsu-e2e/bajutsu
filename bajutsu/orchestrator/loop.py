"""The deterministic Tier-2 run loop: act -> (wait) -> verify, per step.

Pass/fail comes from machine assertions only; no AI is involved. Execution stops at the first
failure. Backend-agnostic via base.Driver (real driver or FakeDriver); evidence, relaunch, and
device control are injected by the runner.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping

from bajutsu import assertions, interp, intervals
from bajutsu.assertions import AssertionResult, GoldenContext, SchemaContext, VisualContext
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink, NullSink
from bajutsu.mailbox import extract_value, select
from bajutsu.orchestrator.actions import _action_of, _do_action, _step_label
from bajutsu.orchestrator.evidence_rules import (
    _collect_captures,
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
    StepOutcome,
    _no_network,
    scenario_slug,
)
from bajutsu.orchestrator.waits import _wait
from bajutsu.scenario import Assertion, Email, ForEach, If, Scenario, Selector, Step
from bajutsu.webview import DomSource, WebContextDriver

# How often `email` re-polls the mailbox. Unlike the UI's 50 ms `_POLL`, each tick is a remote HTTP
# request to a (often rate-limited / metered) provider, so it polls about once a second.
_EMAIL_POLL = 1.0


def _fail_reason(results: list[AssertionResult]) -> str:
    return "; ".join(r.reason for r in results if not r.ok)


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
    golden_context: GoldenContext | None = None,
) -> tuple[bool, str, list[AssertionResult]]:
    """Execute one step's effect, returning (ok, reason, assertion_results).

    The caller is responsible for interpolation (``_interp_step``) before
    calling this function."""
    try:
        if kind == "wait":
            assert step.wait is not None
            ok, reason = _wait(driver, step.wait, clock, network)
            return ok, reason, []
        if kind == "email":
            assert step.email is not None
            ok, reason = _do_email(step.email, clock, mailbox, bindings)
            return ok, reason, []
        if kind == "assert_":
            assert step.assert_ is not None
            clip = _clipboard_for(step.assert_, control)
            results = assertions.evaluate(
                driver.query(),
                step.assert_,
                network(),
                clipboard=clip,
                golden_context=golden_context,
            )
            ok = assertions.passed(results)
            return ok, "" if ok else _fail_reason(results), results
        _do_action(driver, step, relaunch, control, bindings)
        return True, "", []
    except (base.SelectorError, base.UnsupportedAction, NotImplementedError) as e:
        return False, str(e), []


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
    visual_context: VisualContext | None = None,
    schema_context: SchemaContext | None = None,
    mailbox: MailboxReader | None = None,
    golden_context: GoldenContext | None = None,
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
            golden_context,
            webview_bridge,
        )
        if failure is None and scenario.expect:
            expect = _interp_asserts(scenario.expect, live_bindings)
            clip = _clipboard_for(expect, control)
            if visual_context is not None:
                driver.screenshot(str(visual_context.screenshot_path))
            expect_results = assertions.evaluate(
                driver.query(),
                expect,
                network(),
                visual_context=visual_context,
                schema_context=schema_context,
                clipboard=clip,
                golden_context=golden_context,
            )
            if not assertions.passed(expect_results) and on_blocked is not None:
                event = on_blocked(driver)
                if event is not None:
                    expect_alerts.append(event)
                    if visual_context is not None:
                        driver.screenshot(str(visual_context.screenshot_path))
                    # Re-read the clipboard too: clearing the block may have let the app update the
                    # pasteboard, so the retry must compare against the fresh value, not the stale one.
                    clip = _clipboard_for(expect, control)
                    expect_results = assertions.evaluate(
                        driver.query(),
                        expect,
                        network(),
                        visual_context=visual_context,
                        schema_context=schema_context,
                        clipboard=clip,
                        golden_context=golden_context,
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
    golden_context: GoldenContext | None = None,
    webview_bridge: DomSource | None = None,
) -> str | None:
    """Run the step loop, appending outcomes; return the failure string or None.

    ``bindings`` is a mutable dict (guaranteed by ``run_scenario``) — extract
    steps add ``vars.*`` entries so that subsequent steps and scenario-level
    ``expect`` can reference them."""
    assert bindings is not None
    counter = _StepCounter()

    def exec_steps(steps: list[Step], active_driver: base.Driver) -> str | None:
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
                            failure = exec_steps(step.web.steps, web_driver)
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
            before = active_driver.query() if wants_screen_changed else None
            ok, reason, results = _run_step_body(
                active_driver,
                interp_step,
                kind,
                clock,
                network,
                relaunch,
                bindings,
                control,
                mailbox,
                golden_context,
            )
            if not ok and on_blocked is not None:
                event = on_blocked(active_driver)
                if event is not None:
                    outcome.alerts.append(event)
                    ok, reason, results = _run_step_body(
                        active_driver,
                        interp_step,
                        kind,
                        clock,
                        network,
                        relaunch,
                        bindings,
                        control,
                        mailbox,
                        golden_context,
                    )
            outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
            outcome.duration_s = clock.now() - start

            after = active_driver.query()
            screen_changed = before is not None and after != before

            if outcome.ok and interp_step.extract:
                ext_ok, ext_reason = _run_extract(after, interp_step.extract, bindings)
                if not ext_ok:
                    outcome.ok, outcome.reason = False, ext_reason

            fired = _collect_captures(scenario, step, kind, outcome.ok, screen_changed)
            # Interval kinds are recorded scenario-wide (run_scenario), so only the
            # instant kinds are captured per step here.
            instant = [t for t in fired if _kind_of(t) not in intervals.INTERVAL_KINDS]
            outcome.artifacts.extend(sink.capture(driver, step_id, instant, elements=after))

            outcomes.append(outcome)
            if not outcome.ok:
                return f"step {idx} ({kind}): {outcome.reason}"
        return None

    return exec_steps(scenario.steps, driver)

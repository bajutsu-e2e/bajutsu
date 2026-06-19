"""The deterministic Tier-2 run loop: act -> (wait) -> verify, per step.

Pass/fail comes from machine assertions only; no AI is involved. Execution stops at the first
failure. Backend-agnostic via base.Driver (real driver or FakeDriver); evidence, relaunch, and
device control are injected by the runner.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from bajutsu import assertions, interp, intervals
from bajutsu.assertions import AssertionResult, VisualContext
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink, NullSink
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
from bajutsu.scenario import ForEach, If, Scenario, Selector, Step


def _fail_reason(results: list[AssertionResult]) -> str:
    return "; ".join(r.reason for r in results if not r.ok)


def _run_step_body(
    driver: base.Driver,
    step: Step,
    kind: str,
    clock: Clock,
    network: NetworkSource,
    relaunch: RelaunchFn | None = None,
    bindings: dict[str, str] | None = None,
    control: DeviceControl | None = None,
) -> tuple[bool, str, list[AssertionResult]]:
    """Execute one step's effect, returning (ok, reason, assertion_results).

    The caller is responsible for interpolation (``_interp_step``) before
    calling this function."""
    try:
        if kind == "wait":
            assert step.wait is not None
            ok, reason = _wait(driver, step.wait, clock, network)
            return ok, reason, []
        if kind == "assert_":
            assert step.assert_ is not None
            results = assertions.evaluate(driver.query(), step.assert_, network())
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
) -> RunResult:
    """Run one scenario deterministically, firing capturePolicy rules into `sink`.

    The whole scenario is screen-recorded (always on): the sink starts a video before
    the first step and finalizes it after verification, attaching it to the result.

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
        )
        if failure is None and scenario.expect:
            expect = _interp_asserts(scenario.expect, live_bindings)
            if visual_context is not None:
                driver.screenshot(str(visual_context.screenshot_path))
            expect_results = assertions.evaluate(
                driver.query(), expect, network(), visual_context=visual_context
            )
            if not assertions.passed(expect_results) and on_blocked is not None:
                event = on_blocked(driver)
                if event is not None:
                    expect_alerts.append(event)
                    if visual_context is not None:
                        driver.screenshot(str(visual_context.screenshot_path))
                    expect_results = assertions.evaluate(
                        driver.query(), expect, network(), visual_context=visual_context
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


_ExecSteps = Callable[[list[Step]], str | None]


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
    failure = exec_steps(branch)
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
        failure = exec_steps(loop.steps)
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
) -> str | None:
    """Run the step loop, appending outcomes; return the failure string or None.

    ``bindings`` is a mutable dict (guaranteed by ``run_scenario``) — extract
    steps add ``vars.*`` entries so that subsequent steps and scenario-level
    ``expect`` can reference them."""
    assert bindings is not None
    step_counter = [0]  # mutable counter shared across recursive exec_steps calls

    def exec_steps(steps: list[Step]) -> str | None:
        for step in steps:
            kind = _action_of(step)
            idx = step_counter[0]
            step_counter[0] += 1
            outcome = StepOutcome(index=idx, action=kind)
            if progress is not None:
                progress(f"{sid} · step {idx + 1}: {_step_label(step, kind)}")
            step_id = f"{sid}/{step.name or f'step{idx}'}"
            start = clock.now()
            outcome.started_at = max(0.0, start - scenario_start)

            if kind == "if_":
                assert step.if_ is not None
                ok, reason = _run_if(driver, step.if_, clock, network, bindings, exec_steps)
                outcome.ok, outcome.reason = ok, reason
                outcome.duration_s = clock.now() - start
                outcomes.append(outcome)
                if not ok:
                    return f"step {idx} ({kind}): {reason}"
                continue

            if kind == "for_each":
                assert step.for_each is not None
                ok, reason = _run_for_each(driver, step.for_each, bindings, exec_steps)
                outcome.ok, outcome.reason = ok, reason
                outcome.duration_s = clock.now() - start
                outcomes.append(outcome)
                if not ok:
                    return f"step {idx} ({kind}): {reason}"
                continue

            interp_step = _interp_step(step, bindings)
            before = driver.query() if wants_screen_changed else None
            ok, reason, results = _run_step_body(
                driver, interp_step, kind, clock, network, relaunch, bindings, control
            )
            if not ok and on_blocked is not None:
                event = on_blocked(driver)
                if event is not None:
                    outcome.alerts.append(event)
                    ok, reason, results = _run_step_body(
                        driver, interp_step, kind, clock, network, relaunch, bindings, control
                    )
            outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
            outcome.duration_s = clock.now() - start

            after = driver.query()
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

    return exec_steps(scenario.steps)

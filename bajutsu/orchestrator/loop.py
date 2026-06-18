"""The deterministic Tier-2 run loop: act -> (wait) -> verify, per step.

Pass/fail comes from machine assertions only; no AI is involved. Execution stops at the first
failure. Backend-agnostic via base.Driver (real driver or FakeDriver); evidence, relaunch, and
device control are injected by the runner.
"""

from __future__ import annotations

from collections.abc import Mapping

from bajutsu import assertions, intervals
from bajutsu.assertions import AssertionResult, VisualContext
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink, NullSink
from bajutsu.orchestrator.actions import _action_of, _do_action, _step_label
from bajutsu.orchestrator.evidence_rules import _collect_captures, _kind_of, _run_extract
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
from bajutsu.scenario import Scenario, Step

_SCENARIO_INTERVALS = ("video", "deviceLog", "appTrace")


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
    recordings = sink.start_scenario_intervals(sid, list(_SCENARIO_INTERVALS))
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

    ``bindings`` is a mutable dict — extract steps add ``vars.*`` entries so that
    subsequent steps and scenario-level ``expect`` can reference them."""
    failure: str | None = None
    total = len(scenario.steps)
    for i, step in enumerate(scenario.steps):
        kind = _action_of(step)
        outcome = StepOutcome(index=i, action=kind)
        if progress is not None:
            progress(f"{sid} · step {i + 1}/{total}: {_step_label(step, kind)}")
        interp = _interp_step(step, bindings or {})
        step_id = f"{sid}/{step.name or f'step{i}'}"
        before = driver.query() if wants_screen_changed else None
        start = clock.now()
        outcome.started_at = max(0.0, start - scenario_start)
        ok, reason, results = _run_step_body(
            driver, interp, kind, clock, network, relaunch, bindings, control
        )
        if not ok and on_blocked is not None:
            event = on_blocked(driver)
            if event is not None:
                outcome.alerts.append(event)
                ok, reason, results = _run_step_body(
                    driver, interp, kind, clock, network, relaunch, bindings, control
                )  # retry
        outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
        outcome.duration_s = clock.now() - start

        after = driver.query()
        screen_changed = before is not None and after != before

        if outcome.ok and interp.extract and bindings is not None:
            ext_ok, ext_reason = _run_extract(after, interp.extract, bindings)
            if not ext_ok:
                outcome.ok, outcome.reason = False, ext_reason

        fired = _collect_captures(scenario, step, kind, outcome.ok, screen_changed)
        # Interval kinds are recorded scenario-wide (run_scenario), so only the
        # instant kinds are captured per step here.
        instant = [t for t in fired if _kind_of(t) not in intervals.INTERVAL_KINDS]
        outcome.artifacts.extend(sink.capture(driver, step_id, instant, elements=after))

        outcomes.append(outcome)
        if not outcome.ok:
            failure = f"step {i} ({kind}): {outcome.reason}"
            break
    return failure

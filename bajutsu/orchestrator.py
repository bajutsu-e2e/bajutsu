"""Orchestrator — the deterministic Tier2 run loop.

Each step runs as act -> (wait) -> verify. Pass/fail comes from machine
assertions only; no AI is involved. Execution stops at the first failure.

This module is backend-agnostic (via base.Driver): it works with a real driver
or the FakeDriver. Evidence and preconditions / relaunch (env integration) are
wired in later.
"""

from __future__ import annotations

import fnmatch
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from bajutsu import assertions, intervals
from bajutsu.assertions import AssertionResult
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink, NullSink
from bajutsu.scenario import CaptureRule, Gone, Scenario, Selector, Step, Wait

_SWIPE_DIST = 100.0
_POLL = 0.05


class Clock(Protocol):
    """Time and sleep (swappable in tests to make waits deterministic)."""

    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass
class StepOutcome:
    index: int
    action: str
    ok: bool = True
    reason: str = ""
    duration_s: float = 0.0
    assertion_results: list[AssertionResult] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)


@dataclass
class RunResult:
    scenario: str
    ok: bool
    steps: list[StepOutcome]
    expect_results: list[AssertionResult] = field(default_factory=list)
    failure: str | None = None


def _action_of(step: Step) -> str:
    for a in ("tap", "long_press", "type", "swipe", "wait", "assert_", "relaunch"):
        if getattr(step, a) is not None:
            return a
    raise AssertionError("step に有効なアクションがない（scenario 検証で保証済み）")


def _center(frame: base.Frame) -> base.Point:
    x, y, w, h = frame
    return (x + w / 2, y + h / 2)


def _target(center: base.Point, direction: str) -> base.Point:
    cx, cy = center
    if direction == "up":
        return (cx, cy - _SWIPE_DIST)
    if direction == "down":
        return (cx, cy + _SWIPE_DIST)
    if direction == "left":
        return (cx - _SWIPE_DIST, cy)
    return (cx + _SWIPE_DIST, cy)  # right


def _exists(elements: list[base.Element], sel: base.Selector) -> bool:
    return len(base.find_all(elements, sel)) >= 1


def _wait(driver: base.Driver, w: Wait, clock: Clock) -> tuple[bool, str]:
    """Condition wait. Polls query() until satisfied instead of a fixed sleep."""
    deadline = clock.now() + w.timeout
    if w.for_ is not None:
        target = w.for_.as_selector()
        while not _exists(driver.query(), target):
            if clock.now() >= deadline:
                return False, f"wait timeout: for {target} ({w.timeout}s)"
            clock.sleep(_POLL)
        return True, ""
    if isinstance(w.until, Gone):
        target = w.until.gone.as_selector()
        while _exists(driver.query(), target):
            if clock.now() >= deadline:
                return False, f"wait timeout: gone {target} ({w.timeout}s)"
            clock.sleep(_POLL)
        return True, ""
    if w.until == "settled":
        return _wait_settled(driver, deadline, clock)
    # until == "screenChanged"
    before = driver.query()
    while driver.query() == before:
        if clock.now() >= deadline:
            return False, f"wait timeout: screenChanged ({w.timeout}s)"
        clock.sleep(_POLL)
    return True, ""


_SETTLE_POLLS = 2  # consecutive unchanged polls that count as "settled"


def _wait_settled(driver: base.Driver, deadline: float, clock: Clock) -> tuple[bool, str]:
    """Wait until a non-empty screen stops changing (transition/animation finished).

    A blank/collapsed tree (e.g. a screen mid-render, or one covered by a system
    alert) is never treated as settled. Best-effort: timing out just proceeds with the
    current screen — a settle is a stabilization hint, not a correctness assertion, so
    it never fails the step.
    """
    previous = driver.query()
    stable = 0
    while stable < _SETTLE_POLLS:
        if clock.now() >= deadline:
            return True, ""
        clock.sleep(_POLL)
        current = driver.query()
        if current == previous and any(el["identifier"] for el in current):
            stable += 1
        else:
            stable, previous = 0, current
    return True, ""


def _do_action(driver: base.Driver, step: Step) -> None:
    """Run tap / longPress / type / swipe / relaunch (wait and assert live in the run loop)."""
    if step.tap is not None:
        driver.tap(step.tap.as_selector())
        return
    if step.long_press is not None:
        driver.long_press(step.long_press.sel.as_selector(), step.long_press.duration)
        return
    if step.type is not None:
        if step.type.into is not None:
            driver.tap(step.type.into.as_selector())
        driver.type_text(step.type.text)
        return
    if step.swipe is not None:
        sw = step.swipe
        if sw.from_ is not None and sw.to is not None:
            driver.swipe(sw.from_, sw.to)
        elif sw.on is not None and sw.direction is not None:
            el = base.resolve_unique(driver.query(), sw.on.as_selector())
            center = _center(el["frame"])
            driver.swipe(center, _target(center, sw.direction))
        return
    if step.relaunch is not None:
        raise NotImplementedError("relaunch は env 統合後（M1 後半）")
    raise AssertionError("未対応アクション")


# on_blocked(driver) -> True if it cleared a blocking condition (e.g. a system
# alert) and the step is worth retrying.
BlockedHandler = Callable[[base.Driver], bool]


def _run_step_body(
    driver: base.Driver, step: Step, kind: str, clock: Clock
) -> tuple[bool, str, list[AssertionResult]]:
    """Execute one step's effect, returning (ok, reason, assertion_results)."""
    try:
        if kind == "wait":
            assert step.wait is not None
            ok, reason = _wait(driver, step.wait, clock)
            return ok, reason, []
        if kind == "assert_":
            assert step.assert_ is not None
            results = assertions.evaluate(driver.query(), step.assert_)
            ok = assertions.passed(results)
            return ok, "" if ok else _fail_reason(results), results
        _do_action(driver, step)
        return True, "", []
    except (base.SelectorError, NotImplementedError) as e:
        return False, str(e), []


def _fail_reason(results: list[AssertionResult]) -> str:
    return "; ".join(r.reason for r in results if not r.ok)


# --- capturePolicy firing (evidence rules) ---

_DSL_ACTION = {"long_press": "longPress", "assert_": "assert"}


def _primary_selector(step: Step) -> Selector | None:
    if step.tap is not None:
        return step.tap
    if step.long_press is not None:
        return step.long_press.sel
    if step.type is not None:
        return step.type.into
    if step.swipe is not None:
        return step.swipe.on
    return None


def _rule_fires(
    rule: CaptureRule, kind: str, primary_id: str | None, screen_changed: bool, ok: bool
) -> bool:
    trigger = rule.on
    if trigger.action is not None:
        if trigger.action != _DSL_ACTION.get(kind, kind):
            return False
        if trigger.id_matches is not None:
            return primary_id is not None and fnmatch.fnmatchcase(primary_id, trigger.id_matches)
        return True
    if trigger.event == "screenChanged":
        return screen_changed
    if trigger.result == "error":
        return not ok
    return False


def _dedupe(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _kind_of(token: str) -> str:
    return token.partition(".")[0]


def _collect_captures(
    scenario: Scenario, step: Step, kind: str, ok: bool, screen_changed: bool
) -> list[str]:
    """Capture kinds fired for this step: inline `capture` plus matching policy rules."""
    fired: list[str] = list(step.capture or [])
    primary = _primary_selector(step)
    primary_id = primary.id if primary is not None else None
    for rule in scenario.capture_policy:
        if _rule_fires(rule, kind, primary_id, screen_changed, ok):
            fired.extend(rule.capture)
    return _dedupe(fired)


def _pre_intervals(scenario: Scenario, step: Step, kind: str) -> list[str]:
    """Interval capture tokens knowable before the step runs (inline + action rules).

    Interval kinds (video / deviceLog) must start before the action, so only triggers
    determinable from the step itself qualify — screenChanged / error fire too late.
    """
    tokens: list[str] = list(step.capture or [])
    primary = _primary_selector(step)
    primary_id = primary.id if primary is not None else None
    for rule in scenario.capture_policy:
        trigger = rule.on
        if trigger.action is not None and trigger.action == _DSL_ACTION.get(kind, kind):
            if trigger.id_matches is None or (
                primary_id is not None and fnmatch.fnmatchcase(primary_id, trigger.id_matches)
            ):
                tokens.extend(rule.capture)
    return [t for t in _dedupe(tokens) if _kind_of(t) in intervals.INTERVAL_KINDS]


def run_scenario(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock | None = None,
    sink: EvidenceSink | None = None,
    on_blocked: BlockedHandler | None = None,
) -> RunResult:
    """Run one scenario deterministically, firing capturePolicy rules into `sink`.

    If a step fails and `on_blocked` clears a blocking condition (e.g. dismisses a
    system alert), the step is retried once before being recorded as a failure.
    """
    clock = clock or RealClock()
    sink = sink or NullSink()
    wants_screen_changed = any(r.on.event == "screenChanged" for r in scenario.capture_policy)
    outcomes: list[StepOutcome] = []
    failure: str | None = None

    for i, step in enumerate(scenario.steps):
        kind = _action_of(step)
        outcome = StepOutcome(index=i, action=kind)
        step_id = step.name or f"step{i}"
        before = driver.query() if wants_screen_changed else None
        running = sink.start_intervals(step_id, _pre_intervals(scenario, step, kind))
        start = clock.now()
        ok, reason, results = _run_step_body(driver, step, kind, clock)
        if not ok and on_blocked is not None and on_blocked(driver):
            ok, reason, results = _run_step_body(driver, step, kind, clock)  # retry once
        outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
        outcome.duration_s = clock.now() - start

        for interval in running:  # stop interval captures now that the step has settled
            outcome.artifacts.append(Artifact(interval.stop().name, interval.kind, interval.provider))
        screen_changed = before is not None and driver.query() != before
        fired = _collect_captures(scenario, step, kind, outcome.ok, screen_changed)
        instant = [t for t in fired if _kind_of(t) not in intervals.INTERVAL_KINDS]
        outcome.artifacts.extend(sink.capture(driver, step_id, instant))

        outcomes.append(outcome)
        if not outcome.ok:
            failure = f"step {i} ({kind}): {outcome.reason}"
            break

    expect_results: list[AssertionResult] = []
    if failure is None and scenario.expect:
        expect_results = assertions.evaluate(driver.query(), scenario.expect)
        if not assertions.passed(expect_results) and on_blocked is not None and on_blocked(driver):
            expect_results = assertions.evaluate(driver.query(), scenario.expect)  # retry once
        if not assertions.passed(expect_results):
            failure = "expect: " + _fail_reason(expect_results)

    return RunResult(
        scenario=scenario.name,
        ok=failure is None,
        steps=outcomes,
        expect_results=expect_results,
        failure=failure,
    )

"""Orchestrator — the deterministic Tier2 run loop.

Each step runs as act -> (wait) -> verify. Pass/fail comes from machine
assertions only; no AI is involved. Execution stops at the first failure.

This module is backend-agnostic (via base.Driver): it works with a real driver
or the FakeDriver. Evidence and preconditions / relaunch (env integration) are
wired in later.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from simpilot import assertions
from simpilot.assertions import AssertionResult
from simpilot.drivers import base
from simpilot.scenario import Gone, Scenario, Step, Wait

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
    # until == "screenChanged"
    before = driver.query()
    while driver.query() == before:
        if clock.now() >= deadline:
            return False, f"wait timeout: screenChanged ({w.timeout}s)"
        clock.sleep(_POLL)
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


def _fail_reason(results: list[AssertionResult]) -> str:
    return "; ".join(r.reason for r in results if not r.ok)


def run_scenario(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock | None = None,
) -> RunResult:
    """Run one scenario deterministically."""
    clock = clock or RealClock()
    outcomes: list[StepOutcome] = []
    failure: str | None = None

    for i, step in enumerate(scenario.steps):
        kind = _action_of(step)
        outcome = StepOutcome(index=i, action=kind)
        start = clock.now()
        try:
            if kind == "wait":
                assert step.wait is not None
                outcome.ok, outcome.reason = _wait(driver, step.wait, clock)
            elif kind == "assert_":
                assert step.assert_ is not None
                outcome.assertion_results = assertions.evaluate(driver.query(), step.assert_)
                outcome.ok = assertions.passed(outcome.assertion_results)
                outcome.reason = "" if outcome.ok else _fail_reason(outcome.assertion_results)
            else:
                _do_action(driver, step)
        except (base.SelectorError, NotImplementedError) as e:
            outcome.ok = False
            outcome.reason = str(e)
        outcome.duration_s = clock.now() - start
        outcomes.append(outcome)
        if not outcome.ok:
            failure = f"step {i} ({kind}): {outcome.reason}"
            break

    expect_results: list[AssertionResult] = []
    if failure is None and scenario.expect:
        expect_results = assertions.evaluate(driver.query(), scenario.expect)
        if not assertions.passed(expect_results):
            failure = "expect: " + _fail_reason(expect_results)

    return RunResult(
        scenario=scenario.name,
        ok=failure is None,
        steps=outcomes,
        expect_results=expect_results,
        failure=failure,
    )

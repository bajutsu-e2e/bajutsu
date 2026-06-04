"""Record loop (Tier 1) — drive an app with an agent and emit a scenario.

observe (query) -> agent proposes the next action -> execute it -> repeat, until
the agent signals done or max_steps is hit. The recorded steps form a deterministic
scenario that `run` later replays with no AI.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from simyoke.agent import Agent, Observation
from simyoke.drivers import base
from simyoke.orchestrator import BlockedHandler, Clock, RealClock, _action_of, _do_action, _wait
from simyoke.scenario import Assertion, Scenario, Step


def _screenshot_bytes(driver: base.Driver) -> bytes | None:
    """Capture a PNG of the current screen as bytes (best-effort)."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        driver.screenshot(path)
        data = Path(path).read_bytes()
        Path(path).unlink(missing_ok=True)
        return data or None
    except Exception:  # noqa: BLE001 — screenshots are optional context for the agent
        return None


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
    driver: base.Driver, guard: BlockedHandler, clock: Clock, max_tries: int = 3
) -> None:
    """Dismiss anything covering the app (e.g. a system alert) before the agent observes.

    The agent acts by element id, but a SpringBoard alert has no queryable id and
    collapses the tree to a window with no identified elements — leaving the agent
    nothing to act on. While the tree stays collapsed, keep asking the guard to clear
    it: an alert caught mid-animation can be missed on the first screenshot.
    """
    for _ in range(max_tries):
        if any(el["identifier"] for el in driver.query()):
            return  # the app is showing actionable elements; nothing blocking
        guard(driver)  # try to dismiss whatever is covering the app
        clock.sleep(0.5)  # let it animate out before re-checking


def _execute_with_recovery(
    driver: base.Driver, step: Step, clock: Clock, guard: BlockedHandler | None
) -> bool:
    """Execute a step; if it fails because a prompt is covering the app, clear it and retry."""
    try:
        _execute(driver, step, clock)
        return True
    except base.SelectorError:
        if guard is None:
            return False
        _clear_blocking(driver, guard, clock)
        try:
            _execute(driver, step, clock)
            return True
        except base.SelectorError:
            return False


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
) -> Scenario:
    """Explore toward `goal` with `agent`, returning the recorded scenario.

    If `alert_guard` is given, an unexpected OS prompt (e.g. iOS "Save Password?")
    that surfaces while authoring is dismissed so the agent keeps a clean view. The
    dismissal is environmental, not a recorded step; replay handles it with
    `run --dismiss-alerts`.
    """
    clock = clock or RealClock()
    steps: list[Step] = []
    expect: list[Assertion] = []

    for _ in range(max_steps):
        if alert_guard is not None:
            _clear_blocking(driver, alert_guard, clock)
        elements = driver.query()
        if alert_guard is not None and not any(el["identifier"] for el in elements):
            # A prompt slipped in after the last clear: don't ask the agent to act on a
            # dead screen (it would hallucinate ids); re-clear on the next iteration.
            clock.sleep(0.3)
            continue
        screenshot = _screenshot_bytes(driver) if with_screenshot else None
        proposal = agent.next_action(
            Observation(goal=goal, screen=elements, history=list(steps), screenshot=screenshot)
        )
        if proposal.done:
            expect = proposal.expect
            settle = _settle_step(expect)
            if settle is not None:
                steps.append(settle)  # let an async screen render before replay verifies
            break
        if proposal.step is None:
            break
        if not _execute_with_recovery(driver, proposal.step, clock, alert_guard):
            break  # the proposed action did not resolve, even after clearing prompts
        steps.append(proposal.step)

    return Scenario(name=name, steps=steps, expect=expect)

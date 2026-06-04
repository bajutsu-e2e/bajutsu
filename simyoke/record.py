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
from simyoke.orchestrator import Clock, RealClock, _action_of, _do_action, _wait
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


def _execute(driver: base.Driver, step: Step, clock: Clock) -> None:
    kind = _action_of(step)
    if kind == "wait":
        assert step.wait is not None
        _wait(driver, step.wait, clock)
    elif kind == "assert_":
        return  # assertions are checks, not actions to perform while recording
    else:
        _do_action(driver, step)


def record(
    driver: base.Driver,
    goal: str,
    agent: Agent,
    *,
    name: str = "recorded",
    max_steps: int = 30,
    clock: Clock | None = None,
    with_screenshot: bool = True,
) -> Scenario:
    """Explore toward `goal` with `agent`, returning the recorded scenario."""
    clock = clock or RealClock()
    steps: list[Step] = []
    expect: list[Assertion] = []

    for _ in range(max_steps):
        screenshot = _screenshot_bytes(driver) if with_screenshot else None
        proposal = agent.next_action(
            Observation(
                goal=goal, screen=driver.query(), history=list(steps), screenshot=screenshot
            )
        )
        if proposal.done:
            expect = proposal.expect
            break
        if proposal.step is None:
            break
        try:
            _execute(driver, proposal.step, clock)
        except base.SelectorError:
            break  # the proposed action did not resolve; stop here
        steps.append(proposal.step)

    return Scenario(name=name, steps=steps, expect=expect)

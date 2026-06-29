"""Enrichment loop (BE-0014) — replay existing steps and propose assertions.

Takes a scenario whose steps are already authored (by capture, the editor, or by hand)
and replays them on a live device so an EnrichmentAgent can observe the screen at each
step and propose the verifying assertions the scenario lacks.
"""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.agent import EnrichmentAgent, EnrichmentProposal, StepContext
from bajutsu.drivers import base
from bajutsu.orchestrator import BlockedHandler, Clock, RealClock, _action_of, _do_action, _wait
from bajutsu.record import _screenshot_bytes, shows_app_ui
from bajutsu.scenario import Scenario, Step

Reporter = Callable[[str], None]


class _ReplayFailed(Exception):
    """A step could not be replayed (wait timeout, selector miss, unsupported action, …)."""


def _execute_step(driver: base.Driver, step: Step, clock: Clock) -> None:
    kind = _action_of(step)
    if kind == "wait":
        assert step.wait is not None
        ok, reason = _wait(driver, step.wait, clock)
        if not ok:
            raise _ReplayFailed(reason)
    elif kind == "assert_":
        return
    else:
        _do_action(driver, step)


def _clear_blocking(
    driver: base.Driver,
    guard: BlockedHandler,
    clock: Clock,
    max_tries: int = 3,
    report: Reporter | None = None,
) -> None:
    say = report or (lambda _msg: None)
    announced = False
    for _ in range(max_tries):
        if shows_app_ui(driver.query()):
            break
        if not announced:
            say("⚠️  screen blocked by a system prompt — asking the alert guard to clear it …")
            announced = True
        guard(driver)
        clock.sleep(0.5)


def enrich(
    driver: base.Driver,
    scenario: Scenario,
    agent: EnrichmentAgent,
    *,
    clock: Clock | None = None,
    with_screenshot: bool = True,
    alert_guard: BlockedHandler | None = None,
    report: Reporter | None = None,
) -> EnrichmentProposal:
    """Replay `scenario`'s steps on `driver` and ask `agent` to propose assertions."""
    clock = clock or RealClock()
    say = report or (lambda _msg: None)
    contexts: list[StepContext] = []

    say(f"enrichment: replaying {len(scenario.steps)} step(s) …")

    for i, step in enumerate(scenario.steps, 1):
        if alert_guard is not None:
            _clear_blocking(driver, alert_guard, clock, report=report)

        say(f"[{i}/{len(scenario.steps)}] replaying step …")
        try:
            _execute_step(driver, step, clock)
        except (
            base.SelectorError,
            base.UnsupportedAction,
            _ReplayFailed,
            AssertionError,
            NotImplementedError,
        ):
            say(f"[{i}/{len(scenario.steps)}] step failed — stopping replay")
            break

        elements = driver.query()
        screenshot = _screenshot_bytes(driver) if with_screenshot else None
        contexts.append(StepContext(step=step, screen=elements, screenshot=screenshot))

    if not contexts:
        say("enrichment: no steps could be replayed — skipping assertion proposal")
        return EnrichmentProposal()

    say(f"enrichment: {len(contexts)} step(s) replayed — proposing assertions …")
    proposal = agent.propose_assertions(scenario, contexts)

    if proposal.note:
        say(f"enrichment: 💭 {proposal.note}")
    say(f"enrichment: ✓ {len(proposal.expect)} assertion(s) proposed")

    return proposal

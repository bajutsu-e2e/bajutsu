"""Enrichment loop (BE-0014) — replay existing steps and propose assertions.

Takes a scenario whose steps are already authored (by capture, the editor, or by hand)
and replays them on a live device so an EnrichmentAgent can observe the screen at each
step and propose the verifying assertions the scenario lacks.
"""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.agent_protocols import EnrichmentAgent, EnrichmentProposal, StepContext
from bajutsu.drivers import base
from bajutsu.orchestrator import BlockedHandler, Clock, RealClock
from bajutsu.orchestrator.types import SelectionState
from bajutsu.record import clear_blocking, execute
from bajutsu.scenario import Scenario
from bajutsu.screenshots import screenshot_bytes

Reporter = Callable[[str], None]


class _ReplayFailed(Exception):
    """A step could not be replayed (wait timeout, selector miss, unsupported action, …)."""


def _raise_on_wait_failure(reason: str) -> None:
    """Wait-failure hook for the shared step executor: a timed-out wait stops the replay.

    Unlike `record`, `enrich` observes each step's settled screen to propose assertions, so a
    step it cannot settle is a hard failure rather than something to record forward past."""
    raise _ReplayFailed(reason)


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

    # One selection shared across the replay, the same contract the run loop threads (BE-0265): a
    # `select` step establishes the selection a later `copy` copies, so an authored select→copy
    # sequence replays here without `copy` restarting from an inactive selection.
    selection = SelectionState()

    for i, step in enumerate(scenario.steps, 1):
        if alert_guard is not None:
            clear_blocking(driver, alert_guard, clock, report=report)

        say(f"[{i}/{len(scenario.steps)}] replaying step …")
        try:
            execute(
                driver, step, clock, on_wait_failure=_raise_on_wait_failure, selection=selection
            )
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
        screenshot = screenshot_bytes(driver) if with_screenshot else None
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

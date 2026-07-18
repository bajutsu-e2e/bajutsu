"""Tests for the enrichment loop (BE-0014 Slice 1)."""

from __future__ import annotations

from bajutsu.agents.enrich import enrich
from bajutsu.agents.protocols import EnrichmentProposal, StepContext
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.scenario import Assertion, Scenario, Step


class FakeEnrichmentAgent:
    """Returns a canned EnrichmentProposal and records the contexts it received."""

    def __init__(self, proposal: EnrichmentProposal) -> None:
        self._proposal = proposal
        self.seen_scenario: Scenario | None = None
        self.seen_contexts: list[StepContext] = []

    def propose_assertions(
        self,
        scenario: Scenario,
        step_contexts: list[StepContext],
    ) -> EnrichmentProposal:
        self.seen_scenario = scenario
        self.seen_contexts = list(step_contexts)
        return self._proposal


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _scenario(steps: list[Step], expect: list[Assertion] | None = None) -> Scenario:
    return Scenario(name="test", steps=steps, expect=expect or [])


class _NoSleep:
    def sleep(self, _seconds: float) -> None:
        return None


class _AdvancingClock:
    """A logical clock that advances on sleep, so a condition wait reaches its deadline fast."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_enrich_replays_steps_and_returns_proposed_assertions() -> None:
    initial = [_el("go", "Go")]
    after_tap = [_el("go", "Go"), _el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = after_tap

    driver = FakeDriver(initial, react=react)
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)

    proposed = [Assertion.model_validate({"exists": {"id": "done"}})]
    agent = FakeEnrichmentAgent(EnrichmentProposal(expect=proposed, note="done is visible"))

    result = enrich(driver, scenario, agent, with_screenshot=False)

    assert result.expect == proposed
    assert result.note == "done is visible"
    # The agent received the original scenario and one step context (after the tap).
    assert agent.seen_scenario is scenario
    assert len(agent.seen_contexts) == 1
    assert agent.seen_contexts[0].step == steps[0]
    assert agent.seen_contexts[0].screen == after_tap


def test_enrich_adds_settle_wait_from_proposal() -> None:
    driver = FakeDriver([_el("go", "Go")])
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)

    proposed_expect = [Assertion.model_validate({"exists": {"id": "result"}})]
    settle = Step.model_validate({"wait": {"for": {"id": "result"}, "timeout": 5.0}})
    agent = FakeEnrichmentAgent(EnrichmentProposal(expect=proposed_expect, settle=settle))

    result = enrich(driver, scenario, agent, with_screenshot=False)

    assert result.expect == proposed_expect
    assert result.settle is not None
    assert result.settle.wait is not None
    assert result.settle.wait.for_ is not None
    assert result.settle.wait.for_.id == "result"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_enrich_stops_on_unresolvable_step() -> None:
    driver = FakeDriver([_el("a", "A")])
    steps = [Step.model_validate({"tap": {"id": "missing"}})]
    scenario = _scenario(steps)

    agent = FakeEnrichmentAgent(EnrichmentProposal())

    result = enrich(driver, scenario, agent, with_screenshot=False)

    # The step could not be replayed — enrichment returns empty proposal, agent was never called.
    assert result.expect == []
    assert agent.seen_scenario is None


def test_enrich_stops_replay_on_a_wait_timeout() -> None:
    """A `wait` whose target never appears fails the replay (BE-0201): unlike record, enrich
    stops rather than recording forward, so the agent never sees the unsettled screen."""
    driver = FakeDriver([_el("a", "A")])  # "target" never appears
    steps = [
        Step.model_validate({"wait": {"for": {"id": "target"}, "timeout": 0.1}}),
        Step.model_validate({"tap": {"id": "a"}}),
    ]
    scenario = _scenario(steps)

    agent = FakeEnrichmentAgent(EnrichmentProposal())

    result = enrich(driver, scenario, agent, clock=_AdvancingClock(), with_screenshot=False)

    # The wait timed out on step 1, so replay stopped before any step settled.
    assert result.expect == []
    assert agent.seen_scenario is None


def test_enrich_partial_replay_calls_agent_with_successful_steps() -> None:
    """When step 2 of 3 fails, the agent still sees step 1's context."""
    initial = [_el("a", "A"), _el("b", "B")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "a":
            d.screen = [_el("a", "A")]  # remove "b" so next tap on "b" fails

    driver = FakeDriver(initial, react=react)
    steps = [
        Step.model_validate({"tap": {"id": "a"}}),
        Step.model_validate({"tap": {"id": "b"}}),
        Step.model_validate({"tap": {"id": "a"}}),
    ]
    scenario = _scenario(steps)

    proposed = [Assertion.model_validate({"exists": {"id": "a"}})]
    agent = FakeEnrichmentAgent(EnrichmentProposal(expect=proposed))

    result = enrich(driver, scenario, agent, with_screenshot=False)

    # Only step 1 succeeded; step 2 failed so replay stopped.
    assert len(agent.seen_contexts) == 1
    assert result.expect == proposed


def test_enrich_preserves_selection_across_steps() -> None:
    """A `select` then `copy` replayed through enrich shares one SelectionState (BE-0265).

    The selection contract lives across steps in the run loop; enrich's replay loop must thread the
    same state so `copy` sees the selection `select` established one step earlier — otherwise each
    step would restart with an inactive selection and `copy` would always raise.
    """
    field: base.Element = {
        "identifier": "form.note",
        "label": None,
        "traits": [],
        "value": "hello",
        "frame": (0.0, 0.0, 100.0, 40.0),
    }
    driver = FakeDriver([field])
    steps = [
        Step.model_validate({"select": {"into": {"id": "form.note"}}}),
        Step.model_validate({"copy": {}}),
    ]
    scenario = _scenario(steps)
    agent = FakeEnrichmentAgent(EnrichmentProposal(expect=[]))

    enrich(driver, scenario, agent, with_screenshot=False)

    # Both steps replayed: select_all then copy_selection actually actuated (copy did not raise).
    assert [a[0] for a in driver.actions] == ["tap", "select_all", "copy_selection"]


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


def test_enrich_streams_progress_to_reporter() -> None:
    initial = [_el("go", "Go")]
    after_tap = [_el("go", "Go"), _el("done", "Done")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = after_tap

    driver = FakeDriver(initial, react=react)
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    agent = FakeEnrichmentAgent(EnrichmentProposal(expect=[]))

    msgs: list[str] = []
    enrich(driver, scenario, agent, with_screenshot=False, report=msgs.append)

    assert any("replay" in m.lower() or "enrich" in m.lower() for m in msgs)


# ---------------------------------------------------------------------------
# Alert guard
# ---------------------------------------------------------------------------


def test_enrich_uses_alert_guard_during_replay() -> None:
    from bajutsu.orchestrator import AlertEvent

    app = {
        "identifier": "App",
        "label": "App",
        "traits": ["application"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }
    driver = FakeDriver([app])  # blocked: only bare app window

    guard_called = {"n": 0}

    def guard(d: base.Driver) -> AlertEvent:
        guard_called["n"] += 1
        assert isinstance(d, FakeDriver)
        d.screen = [_el("go", "Go")]  # unblock
        return AlertEvent(label="Not Now")

    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    agent = FakeEnrichmentAgent(EnrichmentProposal(expect=[]))

    enrich(driver, scenario, agent, clock=_NoSleep(), with_screenshot=False, alert_guard=guard)

    assert guard_called["n"] >= 1

"""Tests for the record loop, driven by a scripted fake agent."""

from __future__ import annotations

from bajutsu.agent import Observation, Proposal
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.record import record, shows_app_ui
from bajutsu.scenario import Assertion, Step, dump_scenarios, load_scenarios


class FakeAgent:
    def __init__(self, proposals: list[Proposal]) -> None:
        self._proposals = proposals
        self._i = 0

    def next_action(self, observation: Observation) -> Proposal:
        proposal = self._proposals[self._i]
        self._i += 1
        return proposal


class LoopAgent:
    """Always proposes tapping `a` (never done) — for max_steps testing."""

    def next_action(self, observation: Observation) -> Proposal:
        return Proposal(step=Step.model_validate({"tap": {"id": "a"}}))


class PlanningAgent(FakeAgent):
    """A fake that also decomposes the goal up front and records the observations it saw."""

    def __init__(self, proposals: list[Proposal], plan_steps: list[str]) -> None:
        super().__init__(proposals)
        self._plan_steps = plan_steps
        self.seen: list[Observation] = []

    def plan(self, goal: str) -> list[str]:
        return self._plan_steps

    def next_action(self, observation: Observation) -> Proposal:
        self.seen.append(observation)
        return super().next_action(observation)


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_record_produces_scenario() -> None:
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    agent = FakeAgent(
        [
            Proposal(step=Step.model_validate({"tap": {"id": "go"}})),
            Proposal(done=True, expect=[Assertion.model_validate({"exists": {"id": "done"}})]),
        ]
    )

    scenario = record(driver, "reach done", agent, name="reach")
    assert scenario.name == "reach"
    # the tap, then an auto-inserted wait for the asserted element so replay settles
    assert len(scenario.steps) == 2
    assert scenario.steps[0].tap is not None
    assert scenario.steps[0].tap.id == "go"
    assert scenario.steps[1].wait is not None and scenario.steps[1].wait.for_ is not None
    assert scenario.steps[1].wait.for_.id == "done"
    assert scenario.expect[0].exists is not None
    assert scenario.expect[0].exists.sel.id == "done"

    # the recorded scenario round-trips through YAML
    reloaded = load_scenarios(dump_scenarios([scenario]))
    assert reloaded[0].steps[0].tap is not None
    assert reloaded[0].steps[0].tap.id == "go"
    assert reloaded[0].steps[1].wait is not None


def test_record_sets_scenario_provenance_from_goal() -> None:
    # The goal is the scenario-level `from:` provenance (BE-0044), and it round-trips.
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(step=Step.model_validate({"tap": {"id": "go"}})), Proposal(done=True)]
    )
    scenario = record(driver, "reach the done screen", agent, name="reach")
    assert scenario.from_ == "reach the done screen"
    assert load_scenarios(dump_scenarios([scenario]))[0].from_ == "reach the done screen"


def test_record_streams_plan_and_feeds_it_to_the_agent() -> None:
    driver = FakeDriver([_el("go", "Go")])
    agent = PlanningAgent(
        [Proposal(step=Step.model_validate({"tap": {"id": "go"}})), Proposal(done=True)],
        plan_steps=["Tap Go", "Confirm the result"],
    )
    msgs: list[str] = []
    record(driver, "reach the result", agent, report=msgs.append)
    joined = "\n".join(msgs)
    assert "plan — reach the result" in joined  # the decomposition is explained to the watcher
    assert "1. Tap Go" in joined and "2. Confirm the result" in joined
    # and the same plan is handed to the agent every turn so it can follow the procedure
    assert agent.seen[0].plan == ["Tap Go", "Confirm the result"]


def test_record_without_a_planning_agent_still_works() -> None:
    """A fake agent with no `plan` method records exactly as before — planning is optional."""
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(step=Step.model_validate({"tap": {"id": "go"}})), Proposal(done=True)]
    )
    scenario = record(driver, "x", agent)
    assert scenario.steps and scenario.steps[0].tap is not None and scenario.steps[0].tap.id == "go"


def test_record_stops_on_unresolvable_action() -> None:
    driver = FakeDriver([_el("a", "A")])
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "missing"}}))])
    scenario = record(driver, "x", agent)
    assert scenario.steps == []  # could not execute -> not recorded


def test_record_respects_max_steps() -> None:
    driver = FakeDriver([_el("a", "A")])
    scenario = record(driver, "x", LoopAgent(), max_steps=3)
    assert len(scenario.steps) == 3


def _vel(label: str | None, traits: list[str]) -> base.Element:
    return {
        "identifier": None,
        "label": label,
        "traits": traits,
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_shows_app_ui_recognizes_label_only_screen() -> None:
    # An app without accessibility identifiers (sample2): label-only elements are still app UI.
    app = _vel("BajutsuSample", ["application"])
    assert shows_app_ui([app, _vel("Get Started", ["button"])]) is True
    assert shows_app_ui([_el("onboarding.start", "Get Started")]) is True  # id-only also counts
    # A tree collapsed under a system alert — only the bare app window, nothing actionable.
    assert shows_app_ui([app]) is False
    assert shows_app_ui([]) is False


class _NoSleep:
    def sleep(self, _seconds: float) -> None:
        return None


def test_alert_guard_activity_is_reported() -> None:
    """When a system prompt collapses the tree, the guard's detection and dismissal are streamed."""
    from bajutsu.orchestrator import AlertEvent

    app = _vel("App", ["application"])
    driver = FakeDriver([app])  # blocked: only the bare app window is visible

    def guard(d: base.Driver) -> AlertEvent:
        assert isinstance(d, FakeDriver)
        d.screen = [app, _vel("Get Started", ["button"])]  # dismissing reveals the app
        return AlertEvent(label="Not Now")

    agent = FakeAgent(
        [
            Proposal(step=Step.model_validate({"tap": {"label": "Get Started"}})),
            Proposal(done=True),
        ]
    )
    msgs: list[str] = []
    record(
        driver,
        "x",
        agent,
        clock=_NoSleep(),
        with_screenshot=False,
        alert_guard=guard,
        report=msgs.append,
    )
    joined = "\n".join(msgs)
    assert "blocked by a system prompt" in joined
    assert "dismissed a system alert" in joined and "Not Now" in joined


def test_alert_guard_not_fired_on_a_label_only_app() -> None:
    """Regression: with the guard on and a no-id app, the screen must not look 'blocked' — else
    the guard fires (a vision call) every turn and the loop never reaches the agent."""
    calls = {"n": 0}

    def guard(_driver: base.Driver) -> None:
        calls["n"] += 1

    driver = FakeDriver([_vel("Get Started", ["button"])])
    agent = FakeAgent(
        [
            Proposal(step=Step.model_validate({"tap": {"label": "Get Started"}})),
            Proposal(done=True),
        ]
    )
    scenario = record(driver, "start", agent, alert_guard=guard)
    assert calls["n"] == 0  # the app showed actionable UI; the guard never had to fire
    assert scenario.steps and scenario.steps[0].tap is not None


def test_settle_wait_targets_value_assertion() -> None:
    driver = FakeDriver([_el("go", "Go")])
    finish = Proposal(
        done=True,
        expect=[Assertion.model_validate({"value": {"sel": {"id": "counter"}, "equals": "2"}})],
    )
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "go"}})), finish])
    scenario = record(driver, "g", agent)
    assert scenario.steps[-1].wait is not None and scenario.steps[-1].wait.for_ is not None
    assert scenario.steps[-1].wait.for_.id == "counter"


def test_no_settle_wait_for_negated_assertion() -> None:
    driver = FakeDriver([_el("go", "Go")])
    finish = Proposal(
        done=True, expect=[Assertion.model_validate({"exists": {"id": "x", "negate": True}})]
    )
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "go"}})), finish])
    scenario = record(driver, "g", agent)
    assert all(step.wait is None for step in scenario.steps)  # nothing positive to wait for

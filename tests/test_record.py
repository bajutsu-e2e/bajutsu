"""Tests for the record loop, driven by a scripted fake agent."""

from __future__ import annotations

from simyoke.agent import Observation, Proposal
from simyoke.drivers import base
from simyoke.drivers.fake import FakeDriver
from simyoke.record import record
from simyoke.scenario import Assertion, Step, dump_scenarios, load_scenarios


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
    agent = FakeAgent([
        Proposal(step=Step.model_validate({"tap": {"id": "go"}})),
        Proposal(done=True, expect=[Assertion.model_validate({"exists": {"id": "done"}})]),
    ])

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


def test_record_stops_on_unresolvable_action() -> None:
    driver = FakeDriver([_el("a", "A")])
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "missing"}}))])
    scenario = record(driver, "x", agent)
    assert scenario.steps == []  # could not execute -> not recorded


def test_record_respects_max_steps() -> None:
    driver = FakeDriver([_el("a", "A")])
    scenario = record(driver, "x", LoopAgent(), max_steps=3)
    assert len(scenario.steps) == 3


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

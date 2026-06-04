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
    assert len(scenario.steps) == 1
    assert scenario.steps[0].tap is not None
    assert scenario.steps[0].tap.id == "go"
    assert scenario.expect[0].exists is not None
    assert scenario.expect[0].exists.sel.id == "done"

    # the recorded scenario round-trips through YAML
    reloaded = load_scenarios(dump_scenarios([scenario]))
    assert reloaded[0].steps[0].tap is not None
    assert reloaded[0].steps[0].tap.id == "go"


def test_record_stops_on_unresolvable_action() -> None:
    driver = FakeDriver([_el("a", "A")])
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "missing"}}))])
    scenario = record(driver, "x", agent)
    assert scenario.steps == []  # could not execute -> not recorded


def test_record_respects_max_steps() -> None:
    driver = FakeDriver([_el("a", "A")])
    scenario = record(driver, "x", LoopAgent(), max_steps=3)
    assert len(scenario.steps) == 3

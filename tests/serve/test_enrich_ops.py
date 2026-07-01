"""Tests for the enrichment operations layer (BE-0014 Slice 2).

Operations-level tests with FakeDriver + FakeEnrichmentAgent — no HTTP, no Simulator, no LLM.
"""

from __future__ import annotations

from pathlib import Path

from _shared import project

from bajutsu.agent import EnrichmentProposal, StepContext
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.scenario import Assertion, Scenario
from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import ServeState


class FakeEnrichmentAgent:
    """Returns a canned proposal; records what it saw."""

    def __init__(self, proposal: EnrichmentProposal) -> None:
        self._proposal = proposal
        self.called = False

    def propose_assertions(
        self,
        scenario: Scenario,
        step_contexts: list[StepContext],
    ) -> EnrichmentProposal:
        self.called = True
        return self._proposal


def _screen() -> list[base.Element]:
    return [
        {
            "identifier": "home.title",
            "label": "Title",
            "traits": ["staticText"],
            "value": None,
            "frame": (0.0, 0.0, 320.0, 44.0),
        },
    ]


def _state_with_config(tmp_path: Path) -> ServeState:
    scn_dir, cfg, runs = project(tmp_path)
    return ServeState(runs_dir=runs, config=cfg, scenarios_dir=scn_dir, cwd=tmp_path)


def _factories(driver: FakeDriver, agent: FakeEnrichmentAgent) -> dict[str, object]:
    return {
        "driver_factory": lambda _t, _b, _u: driver,
        "agent_factory": lambda: agent,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_start_enrich_returns_proposed_assertions(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())
    proposed = [Assertion.model_validate({"exists": {"id": "home.title"}})]
    agent = FakeEnrichmentAgent(EnrichmentProposal(expect=proposed, note="title visible"))

    payload, status = ops.start_enrich(
        state,
        {"target": "demo", "scenario": "smoke.yaml", "name": "alpha"},
        **_factories(driver, agent),
    )

    assert status == 200
    assert payload["ok"] is True
    assert len(payload["expect"]) == 1
    assert payload["expect"][0]["exists"]["sel"]["id"] == "home.title"
    assert payload["note"] == "title visible"
    assert agent.called


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_start_enrich_requires_config(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", config=None)
    payload, status = ops.start_enrich(state, {"target": "demo", "scenario": "smoke.yaml"})
    assert status == 400
    assert "config" in payload["error"]


def test_start_enrich_requires_target(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    payload, status = ops.start_enrich(state, {"scenario": "smoke.yaml"})
    assert status == 400
    assert "target" in payload["error"]


def test_start_enrich_requires_scenario(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    payload, status = ops.start_enrich(state, {"target": "demo"})
    assert status == 400
    assert "scenario" in payload["error"]


def test_start_enrich_unknown_target(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())
    agent = FakeEnrichmentAgent(EnrichmentProposal())

    payload, status = ops.start_enrich(
        state,
        {"target": "nonexistent", "scenario": "smoke.yaml"},
        **_factories(driver, agent),
    )
    assert status == 400
    assert "unknown target" in payload["error"]


def test_start_enrich_scenario_not_found(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())
    agent = FakeEnrichmentAgent(EnrichmentProposal())

    payload, status = ops.start_enrich(
        state,
        {"target": "demo", "scenario": "nonexistent.yaml"},
        **_factories(driver, agent),
    )
    assert status == 404
    assert "not found" in payload["error"]

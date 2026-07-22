"""Tests for the enrichment operations layer (BE-0014 Slice 2).

Operations-level tests with FakeDriver + FakeEnrichmentAgent — no HTTP, no Simulator, no LLM.
"""

from __future__ import annotations

from pathlib import Path

from _shared import project

from bajutsu.agents.protocols import EnrichmentProposal, StepContext
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.scenario import Assertion, Scenario
from bajutsu.serve import operations as ops
from bajutsu.serve.state import ServeState


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
        "driver_factory": lambda _e, _b, _u: (driver, lambda: None),
        "agent_factory": lambda: agent,
    }


def _ios_state(tmp_path: Path) -> ServeState:
    """A ServeState whose `ios` target declares `backend: [ios]` (BE-0267)."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: alpha\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8"
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        f"  ios: {{ bundleId: com.example.ios, backend: [ios], scenarios: {scn_dir} }}\n"
        f"  xcuitest_only: {{ bundleId: com.example.x, backend: [xcuitest], scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    return ServeState(runs_dir=runs, config=cfg, scenarios_dir=scn_dir, cwd=tmp_path)


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
# Actuator selection (BE-0267) — mirrors tests/serve/test_capture_ops.py
# ---------------------------------------------------------------------------


def _recording_factory(seen: list[str]) -> object:
    """A recording factory resolving the backends list the way the default factory does."""
    from bajutsu import backends

    def factory(_eff: object, backends_list: list[str], _udid: str) -> tuple[FakeDriver, object]:
        seen.append(backends.select_actuator_cost_first(backends_list, available=lambda a: True))
        return FakeDriver(_screen()), (lambda: None)

    return factory


def test_start_enrich_ios_target_resolves_to_xcuitest(tmp_path: Path) -> None:
    # A `[ios]` target selects XCUITest — the sole iOS actuator (BE-0290).
    # enrich resolves the backends list it hands the factory the same way capture does.
    seen: list[str] = []
    state = _ios_state(tmp_path)
    agent = FakeEnrichmentAgent(EnrichmentProposal())
    _payload, status = ops.start_enrich(
        state,
        {"target": "ios", "scenario": "smoke.yaml"},
        driver_factory=_recording_factory(seen),
        agent_factory=lambda: agent,
    )
    assert status == 200
    assert seen == ["xcuitest"]


def test_start_enrich_single_actuator_target_unchanged(tmp_path: Path) -> None:
    # A single-actuator target is a hard pin: enrich hands the factory exactly its backend list.
    seen: list[list[str]] = []

    def factory(_eff: object, backends_list: list[str], _udid: str) -> tuple[FakeDriver, object]:
        seen.append(backends_list)
        return FakeDriver(_screen()), (lambda: None)

    state = _ios_state(tmp_path)
    agent = FakeEnrichmentAgent(EnrichmentProposal())
    _payload, status = ops.start_enrich(
        state,
        {"target": "xcuitest_only", "scenario": "smoke.yaml"},
        driver_factory=factory,
        agent_factory=lambda: agent,
    )
    assert status == 200
    assert seen == [["xcuitest"]]


def test_start_enrich_explicit_body_backend_wins(tmp_path: Path) -> None:
    # An explicit `backend` in the request body is passed through as a single-element list,
    # overriding the target's own `[ios]` config — the `[backend] if backend else ...` TRUE
    # branch (BE-0267) that target-driven tests never reach.
    seen: list[list[str]] = []

    def factory(_eff: object, backends_list: list[str], _udid: str) -> tuple[FakeDriver, object]:
        seen.append(backends_list)
        return FakeDriver(_screen()), (lambda: None)

    state = _ios_state(tmp_path)
    agent = FakeEnrichmentAgent(EnrichmentProposal())
    _payload, status = ops.start_enrich(
        state,
        {"target": "ios", "scenario": "smoke.yaml", "backend": "xcuitest"},
        driver_factory=factory,
        agent_factory=lambda: agent,
    )
    assert status == 200
    assert seen == [["xcuitest"]]


def test_start_enrich_explicit_alias_backend_resolves(tmp_path: Path) -> None:
    # An explicit alias like `backend: "ios"` still goes through the selector, not a raw passthrough:
    # the override branch hands the factory `["ios"]`, which resolves to XCUITest (BE-0267, BE-0290).
    seen: list[str] = []
    state = _ios_state(tmp_path)
    agent = FakeEnrichmentAgent(EnrichmentProposal())
    _payload, status = ops.start_enrich(
        state,
        {"target": "xcuitest_only", "scenario": "smoke.yaml", "backend": "ios"},
        driver_factory=_recording_factory(seen),
        agent_factory=lambda: agent,
    )
    assert status == 200
    assert seen == ["xcuitest"]


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

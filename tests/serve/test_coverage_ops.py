"""Tests for the coverage operations layer (BE-0146).

Operations-level tests for the `POST /api/coverage` endpoint that surfaces the deterministic
`bajutsu coverage` aggregation (BE-0050) in the serve Web UI — no HTTP, no Simulator, no AI.
"""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import ServeState

# A suite whose scenarios reference ids under two namespaces (`home`, `cart`); a third declared
# namespace (`settings`) is touched by no scenario, so it is the gap the map should surface.
_SCENARIO = "- name: browse\n  steps:\n    - tap: { id: home.title }\n    - tap: { id: cart.add }\n"


def _state(tmp_path: Path, *, id_namespaces: list[str] | None = None) -> ServeState:
    """A ServeState over a `demo` target with a scenarios dir; `bare` declares no scenarios dir."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(_SCENARIO, encoding="utf-8")
    ns = f"    idNamespaces: [{', '.join(id_namespaces)}]\n" if id_namespaces else ""
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n"
        f"  demo:\n    bundleId: com.example.demo\n    scenarios: {scn_dir}\n{ns}"
        "  bare: { bundleId: com.example.bare }\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    return ServeState(runs_dir=runs, config=cfg, cwd=tmp_path)


def _write_run(
    runs: Path, run_id: str, step: str, *, network: list[dict], elements: list[dict]
) -> None:
    step_dir = runs / run_id / step
    step_dir.mkdir(parents=True)
    (step_dir / "network.json").write_text(json.dumps(network), encoding="utf-8")
    (step_dir / "elements.json").write_text(json.dumps(elements), encoding="utf-8")


def test_no_config_returns_400(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    state = ServeState(runs_dir=runs, config=None, cwd=tmp_path)
    payload, status = ops.coverage_view(state, {"target": "demo"})
    assert status == 400
    assert "error" in payload


def test_missing_target_returns_400(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.coverage_view(state, {})
    assert status == 400
    assert "target" in payload["error"]


def test_unknown_target_returns_400(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.coverage_view(state, {"target": "nope"})
    assert status == 400
    assert "unknown target" in payload["error"]


def test_target_without_scenarios_dir_returns_400(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.coverage_view(state, {"target": "bare"})
    assert status == 400
    assert "scenarios" in payload["error"]


def test_static_coverage_reports_namespaces_and_gap(tmp_path: Path) -> None:
    state = _state(tmp_path, id_namespaces=["home", "cart", "settings"])
    payload, status = ops.coverage_view(state, {"target": "demo"})
    assert status == 200
    assert payload["target"] == "demo"
    static = payload["static"]
    covered = {ns["namespace"] for ns in static["namespaces"]}
    assert covered == {"home", "cart"}
    assert static["gaps"] == ["settings"]
    assert static["total"] == 3
    assert static["covered"] == 2
    # No run set was selected, so the run-evidence dimensions are absent.
    assert "endpoints" not in payload
    assert "observed_ids" not in payload
    # The self-contained HTML report carries the same figures for the browser to render.
    assert "home" in payload["html"]
    assert "settings" in payload["html"]


def test_run_set_folds_in_endpoint_and_observed_dimensions(tmp_path: Path) -> None:
    state = _state(tmp_path, id_namespaces=["home", "cart", "settings"])
    _write_run(
        state.runs_dir,
        "r1",
        "s1",
        network=[
            {
                "method": "GET",
                "url": "https://api.example.com/items",
                "path": "/items",
                "status": 200,
            }
        ],
        elements=[{"identifier": "settings.toggle"}],
    )
    payload, status = ops.coverage_view(state, {"target": "demo", "runs": ["r1"]})
    assert status == 200
    # The observed endpoint has no matching network assertion in the suite, so it is unasserted.
    assert payload["endpoints"]["observed"] == ["GET /items"]
    assert payload["endpoints"]["unasserted"] == ["GET /items"]
    # The run rendered an id under the otherwise-untested `settings` namespace.
    observed_covered = {ns["namespace"] for ns in payload["observed_ids"]["namespaces"]}
    assert observed_covered == {"settings"}


def test_malformed_scenario_returns_400(tmp_path: Path) -> None:
    """An unreadable/invalid scenario file surfaces as a 400, not a traceback."""
    state = _state(tmp_path, id_namespaces=["home"])
    (tmp_path / "scenarios" / "broken.yaml").write_text("steps: [: :", encoding="utf-8")
    payload, status = ops.coverage_view(state, {"target": "demo"})
    assert status == 400
    assert "scenarios" in payload["error"]


def test_run_ids_are_confined_to_single_segments(tmp_path: Path) -> None:
    """A crafted run id must not let the reader glob outside the runs dir."""
    state = _state(tmp_path, id_namespaces=["home"])
    payload, status = ops.coverage_view(state, {"target": "demo", "runs": ["../../etc"]})
    assert status == 400
    assert "run" in payload["error"].lower()


def test_runs_must_be_a_list(tmp_path: Path) -> None:
    """A bare string for `runs` is rejected, not iterated into per-character run ids."""
    state = _state(tmp_path, id_namespaces=["home"])
    payload, status = ops.coverage_view(state, {"target": "demo", "runs": "r1"})
    assert status == 400
    assert "list" in payload["error"]

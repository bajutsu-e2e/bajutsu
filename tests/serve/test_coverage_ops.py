"""Tests for the coverage operations layer (BE-0146).

Operations-level tests for the `POST /api/coverage` endpoint that surfaces the deterministic
`bajutsu coverage` aggregation (BE-0050) in the serve Web UI — no HTTP, no Simulator, no AI.
"""

from __future__ import annotations

import json
from pathlib import Path

from _shared import FakeObjectStore

from bajutsu.serve import operations as ops
from bajutsu.serve.operations.coverage import read_exchanges_via_store
from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
from bajutsu.serve.state import ServeState

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
    runs: Path, run_id: str, sid: str, *, network: list[dict], elements: list[dict]
) -> None:
    """A run's evidence at the real layout (`bajutsu.runner.pipeline`/`bajutsu.evidence`):
    `<sid>/network.json` (scenario-level) and `<sid>/<step_id>/elements.json` (per-step), plus the
    `manifest.json` `coverage_view`'s seam-routed readers derive those paths from (BE-0258)."""
    step_id = f"{sid}/step0"
    step_dir = runs / run_id / step_id
    step_dir.mkdir(parents=True)
    (runs / run_id / sid / "network.json").write_text(json.dumps(network), encoding="utf-8")
    (step_dir / "elements.json").write_text(json.dumps(elements), encoding="utf-8")
    manifest = {
        "runId": run_id,
        "scenarios": [
            {
                "sid": sid,
                "artifacts": [{"name": f"{sid}/network.json", "kind": "network"}],
                "steps": [
                    {"artifacts": [{"name": f"{step_id}/elements.json", "kind": "elements"}]}
                ],
            }
        ],
    }
    (runs / run_id / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


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


def test_run_set_folds_in_dimensions_from_object_storage(tmp_path: Path) -> None:
    """A hosted backend (`ObjectStorageArtifactStore`) folds in the same run-evidence dimensions
    as local `serve` (BE-0258): before this fix, `coverage_view` globbed `state.runs_dir` directly
    and would silently see no evidence at all here, even though it exists in object storage."""
    state = _state(tmp_path, id_namespaces=["home", "cart", "settings"])
    sid, step_id = "s1", "s1/step0"
    manifest = {
        "runId": "r1",
        "scenarios": [
            {
                "sid": sid,
                "artifacts": [{"name": f"{sid}/network.json", "kind": "network"}],
                "steps": [
                    {"artifacts": [{"name": f"{step_id}/elements.json", "kind": "elements"}]}
                ],
            }
        ],
    }
    network = [
        {"method": "GET", "url": "https://api.example.com/items", "path": "/items", "status": 200}
    ]
    objects = {
        "r1/manifest.json": json.dumps(manifest).encode(),
        f"r1/{sid}/network.json": json.dumps(network).encode(),
        f"r1/{step_id}/elements.json": json.dumps([{"identifier": "settings.toggle"}]).encode(),
    }
    state.artifacts = ObjectStorageArtifactStore(  # type: ignore[assignment]
        FakeObjectStore(objects), prefix=""
    )

    payload, status = ops.coverage_view(state, {"target": "demo", "runs": ["r1"]})
    assert status == 200
    assert payload["endpoints"]["observed"] == ["GET /items"]
    observed_covered = {ns["namespace"] for ns in payload["observed_ids"]["namespaces"]}
    assert observed_covered == {"settings"}


def test_malformed_scenario_returns_400(tmp_path: Path) -> None:
    """An unreadable/invalid scenario file surfaces as a 400, not a traceback."""
    state = _state(tmp_path, id_namespaces=["home"])
    (tmp_path / "scenarios" / "broken.yaml").write_text("steps: [: :", encoding="utf-8")
    payload, status = ops.coverage_view(state, {"target": "demo"})
    assert status == 400
    assert "scenarios" in payload["error"]


def test_read_exchanges_via_store_drops_a_batch_with_one_bad_entry_wholesale() -> None:
    """A `network.json` with one invalid exchange mixed among valid ones drops the whole file's
    batch, matching `bajutsu.coverage.read_exchanges`'s "a bad entry never leaves a half-read
    batch" — not a partial batch of just the entries seen before the bad one."""
    manifests = [
        {
            "runId": "r1",
            "scenarios": [
                {"sid": "s1", "artifacts": [{"name": "s1/network.json", "kind": "network"}]}
            ],
        }
    ]
    good = {"method": "GET", "url": "https://api.example.com/a", "path": "/a", "status": 200}
    bad = {"method": "GET", "url": "https://api.example.com/b", "status": "not-a-number"}
    store = ObjectStorageArtifactStore(
        FakeObjectStore({"r1/s1/network.json": json.dumps([good, bad]).encode()}), prefix=""
    )
    assert read_exchanges_via_store(store, manifests) == []


def test_read_exchanges_via_store_skips_an_artifact_the_store_cannot_read() -> None:
    """A store I/O error reading one artifact is skipped, not raised — matching the "unreadable
    ones are skipped" promise the local-`runs_dir` glob readers already make."""

    class _RaisingStore:
        def open_bytes(self, rel: str) -> bytes | None:
            raise OSError("gone")

    manifests = [
        {
            "runId": "r1",
            "scenarios": [
                {"sid": "s1", "artifacts": [{"name": "s1/network.json", "kind": "network"}]}
            ],
        }
    ]
    assert read_exchanges_via_store(_RaisingStore(), manifests) == []  # type: ignore[arg-type]


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

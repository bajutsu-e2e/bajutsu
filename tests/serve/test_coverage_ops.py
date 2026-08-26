"""Tests for the coverage operations layer (BE-0146).

Operations-level tests for the `POST /api/coverage` endpoint and the `GET /coverage` page that
surface the deterministic `bajutsu coverage` aggregation (BE-0050) in the serve Web UI — no HTTP,
no Simulator, no AI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _shared import FakeObjectStore

from bajutsu.crawl import fingerprint as screen_fingerprint
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
    runs: Path,
    run_id: str,
    sid: str,
    *,
    network: list[dict[str, Any]],
    elements: list[dict[str, Any]],
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
    state.artifacts = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="")

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
    batch, matching `bajutsu.analysis.coverage.read_exchanges`'s "a bad entry never leaves a half-read
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


# --- screens dimension: a crawl's discovered screens vs the ones the run set reached (issue #1719) ---


def _write_crawl(runs: Path, run_id: str, nodes: list[dict[str, Any]]) -> None:
    """A crawl run's `screenmap.json` — the discovered denominator the screens dimension measures."""
    d = runs / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "screenmap.json").write_text(json.dumps({"nodes": nodes}), encoding="utf-8")


def test_crawl_folds_in_the_screens_dimension(tmp_path: Path) -> None:
    state = _state(tmp_path, id_namespaces=["home"])
    elements = [{"identifier": "home.title"}]
    _write_run(state.runs_dir, "r1", "s1", network=[], elements=elements)
    visited = screen_fingerprint(elements).value  # type: ignore[arg-type]
    _write_crawl(
        state.runs_dir,
        "c1",
        [{"fingerprint": visited, "ids": ["home.title"]}, {"fingerprint": "nevervisited"}],
    )
    payload, status = ops.coverage_view(state, {"target": "demo", "runs": ["r1"], "crawl": "c1"})
    assert status == 200
    screens = payload["screens"]
    assert screens["total"] == 2 and screens["covered"] == 1
    assert [s["label"] for s in screens["unvisited"]] == ["nevervi"]


def test_no_crawl_leaves_the_screens_dimension_out(tmp_path: Path) -> None:
    state = _state(tmp_path, id_namespaces=["home"])
    _write_run(state.runs_dir, "r1", "s1", network=[], elements=[{"identifier": "home.title"}])
    payload, status = ops.coverage_view(state, {"target": "demo", "runs": ["r1"]})
    assert status == 200
    assert "screens" not in payload


def test_crawl_without_runs_returns_400(tmp_path: Path) -> None:
    """The crawl supplies only the denominator; with no run there is no visited evidence, so the
    dimension would read as a silent 0% rather than a measurement."""
    state = _state(tmp_path, id_namespaces=["home"])
    _write_crawl(state.runs_dir, "c1", [{"fingerprint": "aaa"}])
    payload, status = ops.coverage_view(state, {"target": "demo", "crawl": "c1"})
    assert status == 400
    assert "run" in payload["error"]


def test_crawl_without_a_readable_screen_map_returns_400(tmp_path: Path) -> None:
    """The caller picked the crawl from the history, so dropping the dimension silently would read
    as the feature being broken."""
    state = _state(tmp_path, id_namespaces=["home"])
    _write_run(state.runs_dir, "r1", "s1", network=[], elements=[{"identifier": "home.title"}])
    payload, status = ops.coverage_view(
        state, {"target": "demo", "runs": ["r1"], "crawl": "missing"}
    )
    assert status == 400
    assert "screen map" in payload["error"]


def test_crawl_ids_are_confined_to_single_segments(tmp_path: Path) -> None:
    state = _state(tmp_path, id_namespaces=["home"])
    _write_run(state.runs_dir, "r1", "s1", network=[], elements=[{"identifier": "home.title"}])
    payload, status = ops.coverage_view(
        state, {"target": "demo", "runs": ["r1"], "crawl": "../../etc"}
    )
    assert status == 400
    assert "crawl" in payload["error"]


# --- GET /coverage: the linkable page (issue #1719) ---


def test_coverage_html_renders_the_same_map_as_the_view(tmp_path: Path) -> None:
    state = _state(tmp_path, id_namespaces=["home", "cart", "settings"])
    page, status = ops.coverage_html(state, "demo", None, None)
    assert status == 200
    assert page == ops.coverage_view(state, {"target": "demo"})[0]["html"]


def test_coverage_html_reads_its_run_set_from_a_comma_separated_query(tmp_path: Path) -> None:
    """A query parameter carries no list, so the run set is spelled the way a URL can."""
    state = _state(tmp_path, id_namespaces=["home"])
    _write_run(state.runs_dir, "r1", "s1", network=[], elements=[{"identifier": "home.title"}])
    _write_run(state.runs_dir, "r2", "s2", network=[], elements=[{"identifier": "home.cta"}])
    page, status = ops.coverage_html(state, "demo", "r1, r2", None)
    assert status == 200
    assert "Observed ids" in page and "home.cta" in page


def test_coverage_html_reports_a_bad_input_as_a_page_not_raw_json(tmp_path: Path) -> None:
    state = _state(tmp_path, id_namespaces=["home"])
    page, status = ops.coverage_html(state, None, None, None)
    assert status == 400
    assert page.lower().startswith("<!doctype html>")
    assert "target is required" in page


def test_coverage_html_escapes_the_message_it_reports(tmp_path: Path) -> None:
    """The unknown-target error quotes the caller's own string, so it must not reach the page raw."""
    state = _state(tmp_path, id_namespaces=["home"])
    page, status = ops.coverage_html(state, "<script>alert(1)</script>", None, None)
    assert status == 400
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page

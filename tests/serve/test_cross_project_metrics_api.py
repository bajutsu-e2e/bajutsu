"""The `GET /api/metrics/projects` cross-project comparison endpoint (BE-0226 unit 2).

Exercises the operation over a real `ServeState` wired to a `LocalProjectRegistry` and the on-disk
`LocalArtifactStore` — no stubs — so it proves the true round trip from tagged runs to the JSON the
comparison dashboard (unit 3) will consume. Read-only and deterministic: it re-presents recorded
verdicts, never re-runs one, and no LLM enters the path.
"""

from __future__ import annotations

import json
from pathlib import Path

from _shared import _get_json, _serve
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.project_registry import LocalProjectRegistry
from bajutsu.serve.server.app import make_app


def _write_manifest(runs_dir: Path, run_id: str, *, ok: bool) -> None:
    """A manifest.json as the runner writes it — only the fields the aggregator reads."""
    d = runs_dir / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "scenarios": [{"scenario": "s", "ok": ok, "duration_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )


def test_metrics_view_one_row_per_project_with_headline_json(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_manifest(runs_dir, "20260101-000000", ok=True)
    _write_manifest(runs_dir, "20260102-000000", ok=False)
    _write_manifest(runs_dir, "20260103-000000", ok=True)

    registry = LocalProjectRegistry(tmp_path / "projects.json")
    checkout = registry.add(org_id=DEFAULT_ORG, name="checkout", source=None)
    search = registry.add(org_id=DEFAULT_ORG, name="search", source=None)
    # checkout owns two runs (one failing → 0.5), search owns one (passing → 1.0).
    registry.tag_run(org_id=DEFAULT_ORG, project_id=checkout.id, run_id="20260101-000000")
    registry.tag_run(org_id=DEFAULT_ORG, project_id=checkout.id, run_id="20260102-000000")
    registry.tag_run(org_id=DEFAULT_ORG, project_id=search.id, run_id="20260103-000000")

    state = srv.ServeState(runs_dir=runs_dir, project_registry=registry)
    payload, code = ops.project_metrics_view(state)

    assert code == 200
    assert [row["name"] for row in payload] == ["checkout", "search"]
    assert payload[0] == {
        "project_id": checkout.id,
        "name": "checkout",
        "runs": 2,
        "pass_rate": 0.5,
        "flaky_rate": 0.0,
        "duration_p50_s": 1.0,
        "duration_p95_s": 1.0,
        "trend": [
            {"day": "2026-01-01", "runs": 1, "passed_runs": 1, "pass_rate": 1.0},
            {"day": "2026-01-02", "runs": 1, "passed_runs": 0, "pass_rate": 0.0},
        ],
    }
    assert payload[1]["runs"] == 1
    assert payload[1]["pass_rate"] == 1.0


def test_metrics_view_unrun_project_is_a_blank_row(tmp_path: Path) -> None:
    registry = LocalProjectRegistry(tmp_path / "projects.json")
    registry.add(org_id=DEFAULT_ORG, name="fresh", source=None)

    state = srv.ServeState(runs_dir=tmp_path / "runs", project_registry=registry)
    payload, code = ops.project_metrics_view(state)

    assert code == 200
    assert len(payload) == 1
    assert payload[0]["runs"] == 0
    assert payload[0]["pass_rate"] == 0.0
    assert payload[0]["trend"] == []


def test_metrics_view_no_hub_is_empty(tmp_path: Path) -> None:
    # No project registry wired (single-config serve) — the comparison is empty, not an error.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    assert ops.project_metrics_view(state) == ([], 200)


def _hub_state_with_one_run(tmp_path: Path) -> srv.ServeState:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_manifest(runs_dir, "20260101-000000", ok=True)
    registry = LocalProjectRegistry(tmp_path / "projects.json")
    checkout = registry.add(org_id=DEFAULT_ORG, name="checkout", source=None)
    registry.tag_run(org_id=DEFAULT_ORG, project_id=checkout.id, run_id="20260101-000000")
    return srv.ServeState(runs_dir=runs_dir, project_registry=registry)


def test_stdlib_handler_routes_metrics_projects(tmp_path: Path) -> None:
    server, port = _serve(_hub_state_with_one_run(tmp_path))
    try:
        payload = _get_json(port, "/api/metrics/projects")
    finally:
        server.shutdown()
    assert [row["name"] for row in payload] == ["checkout"]
    assert payload[0]["pass_rate"] == 1.0


def test_fastapi_routes_metrics_projects(tmp_path: Path) -> None:
    client = TestClient(make_app(_hub_state_with_one_run(tmp_path)))
    payload = client.get("/api/metrics/projects").json()
    assert [row["name"] for row in payload] == ["checkout"]
    assert payload[0]["pass_rate"] == 1.0

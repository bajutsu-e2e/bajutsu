"""BE-0102 unit 4: the serve Stats tab aggregates the run history through the existing seams.

The run-id list comes from the system of record when a repository is wired (org-scoped), else the
artifact store; the full `manifest.json` of each run is read from the artifact store either way (the
DB `summary` is only the compact history-list shape). Driven against a real SqlRepository on
in-memory SQLite and a real LocalArtifactStore — no mocks."""

from __future__ import annotations

import json
from pathlib import Path

from _shared import _get, _serve, project
from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve.operations import stats_html
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    return repo


def _write_manifest(
    runs: Path,
    run_id: str,
    *,
    ok: bool,
    scenario_hash: str,
    duration_s: float,
    failure: str | None = None,
) -> None:
    """A full manifest.json (with provenance + durations) as the runner writes it, for the aggregator."""
    d = runs / run_id
    d.mkdir(parents=True)
    scenario: dict[str, object] = {"scenario": "login", "ok": ok, "duration_s": duration_s}
    if failure is not None:
        scenario["failure"] = failure
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "backend": "fake",
                "provenance": {"scenarioHash": scenario_hash},
                "scenarios": [scenario],
            }
        ),
        encoding="utf-8",
    )
    (d / "report.html").write_text("<html></html>", encoding="utf-8")


def test_stats_html_from_artifact_store(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    _write_manifest(runs, "20260101-000000", ok=True, scenario_hash="sha256:a", duration_s=2.0)
    _write_manifest(
        runs, "20260102-000000", ok=False, scenario_hash="sha256:a", duration_s=4.0, failure="boom"
    )
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    html, status = stats_html(state)

    assert status == 200
    assert html.startswith("<!DOCTYPE html>")
    assert "Run stats" in html
    assert "login" in html
    # Verdict flips at a constant fingerprint → flaky, aggregated across both runs.
    assert "50.0% — 1/2 runs passed" in html
    assert "flaky" in html


def test_stats_html_from_repository_is_org_scoped(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    repo = _repo()
    repo.ensure_org("other", slug="other", name="Other")
    # Two runs recorded to the default org, one to another org — the other org's run must not appear.
    _write_manifest(runs, "20260101-000000", ok=True, scenario_hash="sha256:a", duration_s=2.0)
    _write_manifest(runs, "20260102-000000", ok=True, scenario_hash="sha256:a", duration_s=3.0)
    _write_manifest(runs, "20260103-000000", ok=False, scenario_hash="sha256:a", duration_s=9.0)
    repo.record_run(
        RunRecord(
            id="20260101-000000",
            org_id="default",
            status="done",
            ok=True,
            summary={"id": "20260101-000000"},
        )
    )
    repo.record_run(
        RunRecord(
            id="20260102-000000",
            org_id="default",
            status="done",
            ok=True,
            summary={"id": "20260102-000000"},
        )
    )
    repo.record_run(
        RunRecord(
            id="20260103-000000",
            org_id="other",
            status="done",
            ok=False,
            summary={"id": "20260103-000000"},
        )
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, repository=repo
    )

    html, status = stats_html(state)

    assert status == 200
    # Only the default org's two (passing) runs are aggregated; the other org's failing run is absent.
    assert "100.0% — 2/2 runs passed" in html


def test_stats_html_empty(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    html, status = stats_html(state)

    assert status == 200
    assert "No runs to aggregate" in html


def test_stats_route_serves_html_over_http(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    _write_manifest(runs, "20260101-000000", ok=True, scenario_hash="sha256:a", duration_s=2.0)
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        status, body, content_type = _get(port, "/stats")
        assert status == 200
        assert "text/html" in content_type
        assert b"Run stats" in body and b"login" in body
    finally:
        server.shutdown()
        server.server_close()

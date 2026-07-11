"""The serve flakiness panel (BE-0220, Half 1): the ranked flaky-scenario surface over run history.

Read-only and org-scoped, mirroring the stats tab (BE-0102). When a repository is wired the ranking
groups straight from the DB provenance stamp (the BE-0220 prerequisite columns); without one it
builds the same records from each run's `manifest.json`. Driven against a real SqlRepository on
in-memory SQLite and a real LocalArtifactStore — no mocks.
"""

from __future__ import annotations

import json
from pathlib import Path

from _shared import _get, _serve, project
from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve.operations import flakiness_html
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    return repo


def _write_manifest(runs: Path, run_id: str, *, ok: bool, scenario_hash: str = "sha256:a") -> None:
    """A full manifest.json with a provenance stamp, as the runner writes it."""
    d = runs / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "provenance": {"scenarioHash": scenario_hash},
                "scenarios": [{"scenario": "login", "ok": ok}],
            }
        ),
        encoding="utf-8",
    )
    (d / "report.html").write_text("<html></html>", encoding="utf-8")


def test_flakiness_html_from_artifact_store(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    _write_manifest(runs, "20260101-000000", ok=True)
    _write_manifest(runs, "20260102-000000", ok=False)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    html, status = flakiness_html(state)

    assert status == 200
    assert html.startswith("<!DOCTYPE html>")
    assert "Flaky scenarios" in html
    assert "login" in html
    # Verdict flips at a constant fingerprint → flaky, with links to both runs' evidence.
    assert "flaky" in html
    assert "/runs/20260101-000000/report.html" in html
    assert "/runs/20260102-000000/report.html" in html


def test_flakiness_html_from_repository_is_org_scoped(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    repo = _repo()
    repo.ensure_org("other", slug="other", name="Other")
    # Two default-org runs flip the verdict at one fingerprint; another org's run must not appear.
    repo.record_run(
        RunRecord(
            id="20260101-000000",
            org_id="default",
            status="done",
            ok=True,
            summary={"scenarios": ["login"]},
            scenario_hash="sha256:a",
        )
    )
    repo.record_run(
        RunRecord(
            id="20260102-000000",
            org_id="default",
            status="done",
            ok=False,
            summary={"scenarios": ["login"]},
            scenario_hash="sha256:a",
        )
    )
    repo.record_run(
        RunRecord(
            id="20260103-000000",
            org_id="other",
            status="done",
            ok=False,
            summary={"scenarios": ["secret"]},
            scenario_hash="sha256:z",
        )
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, repository=repo
    )

    html, status = flakiness_html(state)

    assert status == 200
    assert "login" in html and "flaky" in html
    # The other org's scenario is never mined.
    assert "secret" not in html and "sha256:z" not in html


def test_flakiness_html_empty(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    html, status = flakiness_html(state)

    assert status == 200
    assert "No runs with a scenario fingerprint" in html


def test_flakiness_route_serves_html_over_http(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    _write_manifest(runs, "20260101-000000", ok=True)
    _write_manifest(runs, "20260102-000000", ok=False)
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        status, body, content_type = _get(port, "/flakiness")
        assert status == 200
        assert "text/html" in content_type
        assert b"Flaky scenarios" in body and b"login" in body
    finally:
        server.shutdown()
        server.server_close()

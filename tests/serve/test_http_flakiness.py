"""The serve flakiness panel (BE-0220, Half 1): the ranked flaky-scenario surface over run history.

Read-only and org-scoped, mirroring the stats tab (BE-0102). When a repository is wired the ranking
groups straight from the DB provenance stamp (the BE-0220 prerequisite columns); without one it
builds the same records from each run's `manifest.json`. Driven against a real SqlRepository on
in-memory SQLite in the gate and, behind the `postgres` marker, against a real Postgres service in
the serve-db.yml lane (BE-0309), plus a real LocalArtifactStore — no mocks.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from _shared import _get, _serve, project
from sqlalchemy import Engine

from bajutsu import serve as srv
from bajutsu.serve.operations import flakiness_html
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base


def _repo(serve_engine: Callable[..., Engine]) -> SqlRepository:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    return repo


def _write_manifest(
    runs: Path,
    run_id: str,
    *,
    ok: bool,
    scenario_hash: str = "sha256:a",
    device_runtime: str = "",
) -> None:
    """A full manifest.json with a provenance stamp, as the runner writes it."""
    d = runs / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "provenance": {"scenarioHash": scenario_hash},
                "scenarios": [{"scenario": "login", "ok": ok, "device_runtime": device_runtime}],
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


def test_flakiness_html_from_repository_is_org_scoped(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    repo = _repo(serve_engine)
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


def test_flakiness_html_backfills_the_device_os_of_rows_recorded_before_the_column(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # BE-0358: a run recorded before `device_runtime` existed carries None, so it would group under
    # the unknown OS while later runs group per OS — splitting one scenario's history at the deploy
    # boundary. The panel repairs those rows from each run's stored manifest, where the per-scenario
    # label already sits, and writes the value back so the repair happens once.
    scn_dir, cfg, runs = project(tmp_path)
    repo = _repo(serve_engine)
    for run_id, ok in (("20260101-000000", True), ("20260102-000000", False)):
        _write_manifest(runs, run_id, ok=ok, device_runtime="iOS 18.6")
        repo.record_run(
            RunRecord(
                id=run_id,
                org_id="default",
                status="done",
                ok=ok,
                summary={"scenarios": ["login"]},
                scenario_hash="sha256:a",
            )
        )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, repository=repo
    )

    html, status = flakiness_html(state)

    assert status == 200
    # Both runs now group under the OS their manifests recorded, so the flip reads as flakiness on
    # that OS rather than as two split, unprovable histories.
    assert "iOS 18.6" in html and "flaky" in html
    assert "no recorded device OS" not in html
    # Repaired for this request only: `record_run` is a full-row upsert, so writing back from a read
    # path would re-insert a run an operator hard-purged between the listing and the repair.
    assert [r.device_runtime for r in repo.list_runs(org_id="default")] == [None, None]


def test_flakiness_html_discloses_a_row_whose_manifest_is_gone(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # Where a deployment no longer holds the manifest there is nothing to backfill from, so the row
    # stays unknown — disclosed in the report rather than passed off as evidence (BE-0358).
    scn_dir, cfg, runs = project(tmp_path)
    repo = _repo(serve_engine)
    for run_id, ok in (("20260101-000000", True), ("20260102-000000", False)):
        repo.record_run(
            RunRecord(
                id=run_id,
                org_id="default",
                status="done",
                ok=ok,
                summary={"scenarios": ["login"]},
                scenario_hash="sha256:a",
            )
        )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, repository=repo
    )

    html, status = flakiness_html(state)

    assert status == 200
    assert "unknown OS" in html and "no single recorded device OS" in html
    # Undetermined, not determined-as-none: a manifest that reappears can still repair the row.
    assert [r.device_runtime for r in repo.list_runs(org_id="default")] == [None, None]


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

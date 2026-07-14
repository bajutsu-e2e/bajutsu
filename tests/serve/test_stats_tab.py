"""BE-0102 unit 4: the serve Stats tab aggregates the run history through the existing seams.

The run-id list comes from the system of record when a repository is wired (org-scoped), else the
artifact store; the full `manifest.json` of each run is read from the artifact store either way (the
DB `summary` is only the compact history-list shape). Driven against a real SqlRepository on
in-memory SQLite and a real LocalArtifactStore; the one exception is a fake ArtifactStore used only
to simulate a read failure at the storage I/O boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _shared import _get, _serve, project
from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve.artifacts import Artifact, LocalArtifactStore
from bajutsu.serve.operations import stats_html
from bajutsu.serve.operations.reads import run_set_manifests
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base
from bajutsu.serve.state import StoreBundle


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


def test_run_set_manifests_reads_explicit_run_set(tmp_path: Path) -> None:
    # The state/actor-free seam BE-0226 reuses to aggregate once per project: given an explicit
    # run-id list and a store, it reads each valid run's manifest and skips ids that are unsafe
    # (a traversal segment) or unreadable (no such run), with no dependence on ServeState.
    _, _, runs = project(tmp_path)
    _write_manifest(runs, "20260101-000000", ok=True, scenario_hash="sha256:a", duration_s=2.0)
    _write_manifest(runs, "20260102-000000", ok=False, scenario_hash="sha256:a", duration_s=4.0)
    store = LocalArtifactStore(runs)

    manifests = run_set_manifests(
        store, ["20260101-000000", "../secret", "20260102-000000", "20260109-000000"]
    )

    assert [m["runId"] for m in manifests] == ["20260101-000000", "20260102-000000"]


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


def test_stats_html_skips_unsafe_run_id_from_repository(tmp_path: Path) -> None:
    # A repository id that isn't a single safe segment must never be turned into a path — it is
    # skipped, so a nested/legacy id can't read outside its run dir (serve's containment model).
    scn_dir, cfg, runs = project(tmp_path)
    repo = _repo()
    _write_manifest(runs, "20260101-000000", ok=True, scenario_hash="sha256:a", duration_s=2.0)
    repo.record_run(
        RunRecord(
            id="20260101-000000/../secret",
            org_id="default",
            status="done",
            ok=True,
            summary={"id": "20260101-000000/../secret"},
        )
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, repository=repo
    )

    html, status = stats_html(state)

    assert status == 200
    assert "No runs to aggregate" in html


class _RaisingArtifactStore:
    """An ArtifactStore whose reads fail — a run deleted between listing and read, or a remote I/O
    error. Only the methods `_run_manifests` touches are exercised (the storage I/O boundary)."""

    def open_bytes(self, rel: str) -> bytes | None:
        raise OSError("gone")

    def exists(self, rel: str) -> bool:
        raise OSError("gone")

    def get(self, rel: str) -> Artifact | None:
        return None

    def list_runs(self) -> list[dict[str, Any]]:
        return []

    def render_report(self, run_id: str) -> Artifact | None:
        return None

    def archive(self, run_id: str) -> Artifact | None:
        return None


def test_stats_html_skips_runs_whose_manifest_read_raises(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    repo = _repo()
    repo.record_run(
        RunRecord(
            id="20260101-000000",
            org_id="default",
            status="done",
            ok=True,
            summary={"id": "20260101-000000"},
        )
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, repository=repo
    )
    # The org's artifact store raises on read; the recorded run must be skipped, not crash the page.
    state.org_stores = lambda org: StoreBundle(
        _RaisingArtifactStore(), state.scenarios, state.baselines, state.secrets
    )

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

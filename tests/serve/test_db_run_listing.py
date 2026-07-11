"""BE-0015 7c-4: a finished run is recorded into the system of record, and the run-history
listing is served from it (org-scoped) when a repository is wired — falling back to the artifact
store otherwise. Driven against a real SqlRepository on in-memory SQLite (no live Postgres, no
mock); `run_job` runs synchronously in the test thread, so the single connection is safe."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _shared import fake_popen, project, write_run
from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve.operations import crawl_runs_payload, runs_payload
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    return repo


def test_run_job_records_finished_run_into_the_repository(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260621-1", ok=True, scenarios=[("alpha", True), ("beta", True)])
    repo = _repo()
    # The actor was upserted at OAuth login, so the run can be attributed to them (the created_by
    # foreign key resolves).
    repo.upsert_user("alice", org_id="default", github_login="alice", email="a@x")
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=repo,
        popen=fake_popen(["PASS  runs/20260621-1/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))
    job.actor = "alice"
    srv.run_job(state, job)

    rec = repo.get_run("20260621-1")
    assert rec is not None
    assert rec.org_id == "default"
    assert rec.status == "done"
    assert rec.created_by == "alice"
    assert rec.ok is True
    # The summary mirrors the artifact listing entry, so a DB-served listing matches the UI shape.
    assert rec.summary["passed"] == 2
    assert rec.summary["total"] == 2
    assert rec.summary["report"] is True


def test_run_job_stamps_run_provenance_from_the_manifest(tmp_path: Path) -> None:
    # BE-0220: the run's manifest.json provenance (the BE-0049 stamp) is mirrored onto the DB record
    # so cross-run flakiness can group by scenario identity straight from the DB.
    scn_dir, cfg, runs = project(tmp_path)
    run_dir = runs / "20260621-4"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "runId": "20260621-4",
                "ok": True,
                "scenarios": [{"scenario": "alpha", "ok": True}],
                "provenance": {
                    "scenarioHash": "sha256:abc123",
                    "toolVersion": "9.9.9",
                    "gitRevision": "deadbeef",
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    repo = _repo()
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=repo,
        popen=fake_popen(["PASS  runs/20260621-4/manifest.json\n"]),
    )
    srv.run_job(state, state.register(srv.Job(cmd=["x"])))

    rec = repo.get_run("20260621-4")
    assert rec is not None
    assert rec.scenario_hash == "sha256:abc123"
    assert rec.tool_version == "9.9.9"
    assert rec.git_revision == "deadbeef"


def test_run_job_leaves_provenance_null_for_a_pre_provenance_run(tmp_path: Path) -> None:
    # A run whose manifest predates the provenance stamp records with null provenance — ungroupable
    # by scenario identity, but never blocking (mirrors audit --history's `skipped`, BE-0049).
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260621-5", ok=True, scenarios=[("alpha", True)])
    repo = _repo()
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=repo,
        popen=fake_popen(["PASS  runs/20260621-5/manifest.json\n"]),
    )
    srv.run_job(state, state.register(srv.Job(cmd=["x"])))

    rec = repo.get_run("20260621-5")
    assert rec is not None
    assert rec.scenario_hash is None
    assert rec.tool_version is None
    assert rec.git_revision is None


def test_run_job_records_a_malformed_manifest_with_null_provenance(tmp_path: Path) -> None:
    # A manifest that parses but isn't a JSON object (a corrupted/partial write left a bare list,
    # string, or `null`) must not abort the whole record_run: the run still persists, just with null
    # provenance and a minimal summary. Guards against `json.loads(...).get(...)` raising
    # AttributeError past the (JSONDecodeError, ValueError) catch and escaping to the outer handler.
    scn_dir, cfg, runs = project(tmp_path)
    run_dir = runs / "20260621-6"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    (run_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    repo = _repo()
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=repo,
        popen=fake_popen(["PASS  runs/20260621-6/manifest.json\n"]),
    )
    srv.run_job(state, state.register(srv.Job(cmd=["x"])))

    rec = repo.get_run("20260621-6")
    assert rec is not None
    assert rec.scenario_hash is None
    assert rec.tool_version is None
    assert rec.git_revision is None


def test_run_job_reads_the_run_manifest_only_once(tmp_path: Path) -> None:
    # `_persist_run` feeds both the history summary and the provenance stamp from a single manifest
    # read. On a hosted backend `open_bytes` is a real object-storage round trip, so reading it once
    # per helper (twice per finished run) doubles exactly the cost `_run_summary` was written to avoid.
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260621-r", ok=True, scenarios=[("alpha", True)])
    repo = _repo()
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=repo,
        popen=fake_popen(["PASS  runs/20260621-r/manifest.json\n"]),
    )

    class CountingStore:
        # A pass-through over the real LocalArtifactStore that tallies manifest reads — the same
        # store-swap seam test_http_artifacts uses to inject a server-style store.
        def __init__(self, inner: object) -> None:
            self._inner = inner
            self.manifest_reads = 0

        def open_bytes(self, rel: str) -> bytes | None:
            if rel.endswith("manifest.json"):
                self.manifest_reads += 1
            return self._inner.open_bytes(rel)  # type: ignore[attr-defined]

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    counting = CountingStore(state.artifacts)
    state.artifacts = counting  # type: ignore[assignment]
    srv.run_job(state, state.register(srv.Job(cmd=["x"])))

    assert repo.get_run("20260621-r") is not None
    assert counting.manifest_reads == 1


def test_run_job_does_not_attribute_to_an_unknown_user(tmp_path: Path) -> None:
    # A run whose actor has no user row (shouldn't happen in practice) is still recorded, just with
    # no created_by — so the foreign key can't break job finalization.
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260621-7", ok=True, scenarios=[("alpha", True)])
    repo = _repo()
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=repo,
        popen=fake_popen(["PASS  runs/20260621-7/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))
    job.actor = "ghost"
    srv.run_job(state, job)

    rec = repo.get_run("20260621-7")
    assert rec is not None
    assert rec.created_by is None


def test_run_job_survives_a_failing_repository(tmp_path: Path) -> None:
    # A repository pointed at a schema-less database raises on record_run. Persistence runs in
    # run_job's finally, just before the log stream is closed, so the error must be swallowed and
    # the job must still finalize (its run id parsed, status done).
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260621-8", ok=True, scenarios=[("alpha", True)])
    engine = create_engine("sqlite://")  # no Base.metadata.create_all → no tables
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=SqlRepository(engine),
        popen=fake_popen(["PASS  runs/20260621-8/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))
    srv.run_job(state, job)  # must not raise
    assert job.view()["status"] == "done"
    assert job.view()["runId"] == "20260621-8"


def test_run_job_without_a_repository_does_not_record(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260621-2", ok=True, scenarios=[("alpha", True)])
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["PASS  runs/20260621-2/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))
    srv.run_job(state, job)  # no repository wired — must not raise
    assert job.view()["runId"] == "20260621-2"


def test_runs_payload_lists_from_the_repository_scoped_to_the_org(tmp_path: Path) -> None:
    _scn_dir, _cfg, runs = project(tmp_path)
    repo = _repo()
    repo.ensure_org("other", slug="other", name="Other")
    repo.record_run(
        RunRecord(
            id="20260621-1",
            org_id="default",
            status="done",
            ok=True,
            created_at=datetime(2026, 6, 21, 9, 0, tzinfo=UTC),
            summary={"id": "20260621-1", "ok": True},
        )
    )
    repo.record_run(
        RunRecord(
            id="20260621-2",
            org_id="default",
            status="done",
            ok=False,
            created_at=datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
            summary={"id": "20260621-2", "ok": False},
        )
    )
    repo.record_run(
        RunRecord(
            id="20260621-3",
            org_id="other",
            status="done",
            ok=True,
            created_at=datetime(2026, 6, 21, 11, 0, tzinfo=UTC),
            summary={"id": "20260621-3", "ok": True},
        )
    )
    state = srv.ServeState(runs_dir=runs, repository=repo)

    payload, status = runs_payload(state)
    assert status == 200
    ids = [r["id"] for r in payload]
    assert ids == ["20260621-2", "20260621-1"]  # newest first, the other org's run excluded


def test_runs_payload_falls_back_to_the_artifact_store_without_a_repository(tmp_path: Path) -> None:
    _scn_dir, _cfg, runs = project(tmp_path)
    write_run(runs, "20260621-9", ok=True, scenarios=[("alpha", True)])
    state = srv.ServeState(runs_dir=runs)

    payload, status = runs_payload(state)
    assert status == 200
    assert [r["id"] for r in payload] == ["20260621-9"]


def test_crawl_runs_payload_falls_back_to_the_artifact_store_without_a_repository(
    tmp_path: Path,
) -> None:
    # Local serve (no repository) resolves to the default org's LocalArtifactStore, which scans
    # runs_dir — today's behavior, preserved after the listing moved onto the ArtifactStore seam.
    _scn_dir, _cfg, runs = project(tmp_path)
    (runs / "20260621-c").mkdir()
    (runs / "20260621-c" / "screenmap.json").write_text(
        '{"nodes": [{}], "edges": [], "crashes": []}', encoding="utf-8"
    )
    payload, status = crawl_runs_payload(srv.ServeState(runs_dir=runs))
    assert status == 200 and [r["id"] for r in payload] == ["20260621-c"]


def test_crawl_runs_payload_is_scoped_to_the_actors_org(tmp_path: Path) -> None:
    # On the server backend the crawl history comes from the actor's org-scoped artifact store, not a
    # local runs_dir scan (BE-0190). Two orgs' stores back distinct dirs; each actor lists only its own.
    from bajutsu.serve.artifacts import LocalArtifactStore
    from bajutsu.serve.state import StoreBundle

    def _write_crawl(root: Path, run_id: str) -> None:
        (root / run_id).mkdir(parents=True)
        (root / run_id / "screenmap.json").write_text(
            '{"nodes": [{}], "edges": [], "crashes": []}', encoding="utf-8"
        )

    dirs = {"default": tmp_path / "acme", "other": tmp_path / "other"}
    _write_crawl(dirs["default"], "20260621-a")
    _write_crawl(dirs["other"], "20260621-b")

    repo = _repo()
    repo.ensure_org("other", slug="other", name="Other")
    repo.upsert_user("al", org_id="default", github_login="al", email="a@x")
    repo.upsert_user("bo", org_id="other", github_login="bo", email="b@x")

    state = srv.ServeState(runs_dir=tmp_path, repository=repo)
    state.org_stores = lambda org: StoreBundle(
        LocalArtifactStore(dirs[org]), state.scenarios, state.baselines, state.secrets
    )

    assert [r["id"] for r in crawl_runs_payload(state, actor="al")[0]] == ["20260621-a"]
    assert [r["id"] for r in crawl_runs_payload(state, actor="bo")[0]] == ["20260621-b"]

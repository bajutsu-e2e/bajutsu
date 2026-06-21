"""BE-0015 7c-4: a finished run is recorded into the system of record, and the run-history
listing is served from it (org-scoped) when a repository is wired — falling back to the artifact
store otherwise. Driven against a real SqlRepository on in-memory SQLite (no live Postgres, no
mock); `run_job` runs synchronously in the test thread, so the single connection is safe."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from _shared import fake_popen, project, write_run
from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve.operations import runs_payload
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
    job = state.new_job(["x"])
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
    job = state.new_job(["x"])
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
    job = state.new_job(["x"])
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
    job = state.new_job(["x"])
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

"""Tests for the run-dispatch concurrency cap (BE-0051 slice 5).

`bajutsu serve` caps how many run/record jobs may run at once so one caller can't monopolize the
scarce device; over the cap, dispatch returns 429.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from _shared import _post, _serve, fake_popen, project
from sqlalchemy import Engine

from bajutsu import serve as srv
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base


def _running(state: srv.ServeState, n: int, start: int = 0) -> None:
    """Seed `n` jobs starting at index `start` using unique `seed_` prefixed keys.

    Using `seed_{i}` keys avoids collisions with integer-keyed jobs that real dispatch may create.
    Use `start` to seed non-overlapping ranges across multiple calls.
    """
    for i in range(start, start + n):
        job_id = f"seed_{i}"
        state.jobs[job_id] = srv.Job(id=job_id, cmd=[], status="running")


def test_active_jobs_counts_only_running() -> None:
    state = srv.ServeState(runs_dir=Path("runs"))
    state.jobs["a"] = srv.Job(id="a", cmd=[], status="running")
    state.jobs["b"] = srv.Job(id="b", cmd=[], status="done")
    assert state.active_jobs() == 1


def test_run_rejected_at_concurrency_cap(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        max_concurrent=2,
        popen=fake_popen(["PASS  runs/x/manifest.json\n"]),
    )
    _running(state, 1)  # below the cap
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/run", {"scenario": "smoke.yaml", "target": "demo"})
        assert status == 200 and "jobId" in resp

        _running(state, 1, start=1)  # now at the cap
        status, resp = _post(port, "/api/run", {"scenario": "smoke.yaml", "target": "demo"})
        assert status == 429 and "too many concurrent jobs" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_record_rejected_at_concurrency_cap(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, max_concurrent=1
    )
    _running(state, 1)  # at the cap
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/record", {"goal": "tap x", "target": "demo"})
        assert status == 429 and "too many concurrent jobs" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_record_allowed_when_under_concurrency_cap(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        max_concurrent=2,
        popen=fake_popen(["PASS  runs/x/manifest.json\n"]),
    )
    _running(state, 1)  # below the cap
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/record", {"goal": "tap x", "target": "demo"})
        assert status == 200 and "jobId" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_unlimited_when_cap_is_zero(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    # 0 = unlimited, so the dispatch spawns a job — stub popen so no real subprocess runs.
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        max_concurrent=0,
        popen=fake_popen(["PASS  runs/x/manifest.json\n"]),
    )
    _running(state, 5)  # would exceed any small cap, but 0 = unlimited
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/run", {"scenario": "smoke.yaml", "target": "demo"})
        assert status == 200 and "jobId" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_cap_releases_a_job_the_jobs_table_finished(
    tmp_path: Path, serve_engine: Callable[..., Engine]
) -> None:
    """A worker-run job that finished in the jobs table stops counting toward the cap.

    Its control-plane `Job` stays "running" by design (BE-0015 W2), so without the jobs-table
    reconciliation every remote run would hold its slot until serve restarts.
    """
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        executor=DbQueueExecutor(repo),
        repository=repo,
        max_concurrent=1,
    )
    first = state.try_register(srv.Job(cmd=[], org="o"))
    assert first is not None
    # No row yet: a job is registered before the executor enqueues it, so a missing row must read as
    # still running — else every dispatch in that window slips past the cap instead of being capped.
    assert state.try_register(srv.Job(cmd=[], org="o")) is None
    repo.enqueue_job(first.id, "o", {})
    assert state.try_register(srv.Job(cmd=[], org="o")) is None  # at the cap
    assert state.active_jobs() == 1

    leased = repo.lease_job("w")
    assert leased is not None and leased.id == first.id
    assert repo.fail_job(first.id, error="boom", worker_id="w")

    assert state.active_jobs() == 0
    assert state.try_register(srv.Job(cmd=[], org="o")) is not None

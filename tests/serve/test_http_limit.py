"""Tests for the run-dispatch concurrency cap (BE-0051 slice 5).

`bajutsu serve` caps how many run/record jobs may run at once so one caller can't monopolize the
scarce device; over the cap, dispatch returns 429.
"""

from __future__ import annotations

from pathlib import Path

from _shared import _post, _serve, fake_popen, project

from bajutsu import serve as srv


def _running(state: srv.ServeState, n: int) -> None:
    """Seed `n` jobs in the running state so the next dispatch sees the cap reached."""
    for i in range(n):
        state.jobs[str(i)] = srv.Job(id=str(i), cmd=[], status="running")


def test_active_jobs_counts_only_running() -> None:
    state = srv.ServeState(runs_dir=Path("runs"))
    state.jobs["a"] = srv.Job(id="a", cmd=[], status="running")
    state.jobs["b"] = srv.Job(id="b", cmd=[], status="done")
    assert state.active_jobs() == 1


def test_run_rejected_at_concurrency_cap(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, max_concurrent=2
    )
    _running(state, 2)  # at the cap
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/run", {"scenario": "smoke.yaml", "app": "demo"})
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
        status, resp = _post(port, "/api/record", {"goal": "tap x", "app": "demo"})
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
        status, resp = _post(port, "/api/record", {"goal": "tap x", "app": "demo"})
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
        status, resp = _post(port, "/api/run", {"scenario": "smoke.yaml", "app": "demo"})
        assert status == 200 and "jobId" in resp
    finally:
        server.shutdown()
        server.server_close()

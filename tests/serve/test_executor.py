"""Tests for the RunExecutor seam (BE-0015 local/server parity, PR1).

`RunExecutor.dispatch` is the one point where local and server hosting diverge: locally a job
runs in-process on a daemon thread (`LocalExecutor`), while a future server backend would enqueue
it for a remote `bajutsu worker`. The job execution body (`run_job`) is unchanged. These tests
pin the local seam with an injected Popen — no server, no macOS needed.
"""

from __future__ import annotations

import time
from pathlib import Path

from _shared import fake_popen, project

from bajutsu import serve as srv


def _wait_done(job: srv.Job, timeout: float = 2.0) -> None:
    """Poll until the dispatched job finishes (it runs on a background thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.view()["status"] == "done":
            return
        time.sleep(0.01)
    raise AssertionError("dispatched job did not finish in time")


def test_serve_state_defaults_to_local_executor(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    assert isinstance(state.executor, srv.LocalExecutor)


def test_local_executor_dispatch_runs_job_to_completion(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))
    state.executor.dispatch(state, job)
    _wait_done(job)
    v = job.view()
    assert v["status"] == "done" and v["ok"] is True
    assert v["runId"] == "20260610-1"
    assert "step 0 ok" in v["lines"]

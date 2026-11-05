"""Tests for the RunExecutor seam (BE-0015, BE-0449).

`RunExecutor.dispatch` hands a created job to whatever executes it. The server never executes one
itself (BE-0449 unit 9): `bajutsu serve` queues every job for a worker, and a state built without a
queue fails each job at dispatch rather than spawning it. These tests pin that default with an
injected Popen that must never be called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _shared import project

from bajutsu import serve as srv


def _never_spawn(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("the server spawned a job child")


def test_serve_state_defaults_to_the_unqueued_executor(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    assert isinstance(state.executor, srv.UnqueuedExecutor)


def test_the_unqueued_executor_fails_the_job_without_spawning_it(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=_never_spawn
    )
    job = state.register(srv.Job(cmd=srv.run_command("smoke.yaml", "demo")))
    state.executor.dispatch(state, job)
    view = job.view()
    # Ended at dispatch, synchronously: no thread, no child, and a line that says why.
    assert view["status"] == "done"
    assert view["ok"] is False
    assert srv.UNQUEUED_LINE in view["lines"]
    # The tail ran, so the live stream is closed rather than left waiting on a run.
    assert state.logbus.final(job.id) is not None
    # And its concurrency slot is free again.
    assert state.active_jobs() == 0

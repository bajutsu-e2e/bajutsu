"""Tests for the queue-based RunExecutor + worker entrypoint (BE-0015 server phase).

`QueueExecutor` is a server implementation of the `RunExecutor` seam: instead of running `run_job`
in-process (like `LocalExecutor`), it serializes the job and enqueues it; a remote `bajutsu worker`
later reconstructs the job and runs the *unchanged* `run_job`. These tests drive both ends with a
fake queue and an injected Popen, so the Linux gate needs neither Redis nor RQ installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _shared import fake_popen, project

from bajutsu import serve as srv
from bajutsu.serve.server.executor import QueueExecutor
from bajutsu.serve.server.worker_job import execute_job_spec, job_spec


class _FakeQueue:
    """Records enqueue calls; stands in for an RQ Queue so tests need no Redis."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[Any, tuple[Any, ...]]] = []

    def enqueue(self, func: Any, *args: Any, **_kw: Any) -> None:
        self.enqueued.append((func, args))


def test_dispatch_enqueues_a_serializable_job_spec(tmp_path: Path) -> None:
    q = _FakeQueue()
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.new_job(
        ["python", "-m", "bajutsu", "run", "--config", "c.yaml"],
        udids=["U1"],
        app_path="A.app",
        build="make build",
    )
    QueueExecutor(q).dispatch(state, job)

    assert len(q.enqueued) == 1
    func, args = q.enqueued[0]
    assert func is execute_job_spec  # RQ enqueues the worker entrypoint by reference
    spec = args[0]
    assert spec == {
        "job_id": job.id,
        "cmd": ["python", "-m", "bajutsu", "run", "--config", "c.yaml"],
        "udids": ["U1"],
        "app_path": "A.app",
        "build": "make build",
    }
    json.dumps(spec)  # must carry no live objects (locks/Popen/bus) — JSON round-trips


def test_job_spec_round_trips_through_json(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.new_job(["run"], udids=["A", "B"], app_path=None, build=None)
    spec = job_spec(job)
    assert json.loads(json.dumps(spec)) == spec


def test_execute_job_spec_rebuilds_and_runs_run_job(tmp_path: Path) -> None:
    # The worker reconstructs a Job + minimal ServeState from the spec and runs the unchanged
    # run_job; an injected Popen stands in for the real `bajutsu run` subprocess.
    project(tmp_path)
    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    job = execute_job_spec(
        spec,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
        cwd=tmp_path,
    )
    v = job.view()
    assert v["status"] == "done" and v["ok"] is True and v["runId"] == "20260610-1"
    assert "step 0 ok" in v["lines"]

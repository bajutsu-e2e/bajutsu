"""Serialize a job for the queue, and run it on the worker (BE-0015 server phase).

`run_job` (the boot → build → run → stream body) is unchanged between local and server hosting.
The only difference is *where* it runs: locally on a thread, on the server on a remote worker that
leased the job from the queue. `job_spec` captures the JSON-serializable fields a worker needs to
reconstruct the job; `execute_job_spec` is the worker entrypoint that rebuilds it and runs it.

No queue/Redis import lives here — this module is safe to import without the ``worker`` extra.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from bajutsu import env
from bajutsu.serve.jobs import Job, ServeState, run_job


def job_spec(job: Job) -> dict[str, Any]:
    """The JSON-serializable description a worker needs to reconstruct and run *job*.

    Only the fields `run_job` reads from the job travel; live objects (the lock, the live Popen,
    the LogBus) are reconstructed worker-side, never serialized."""
    return {
        "job_id": job.id,
        "cmd": list(job.cmd),
        "udids": list(job.udids),
        "app_path": job.app_path,
        "build": job.build,
    }


def execute_job_spec(
    spec: dict[str, Any],
    *,
    popen: Any = subprocess.Popen,
    simctl: env.RunFn = env._real_run,
    cwd: Path | None = None,
) -> Job:
    """Worker entrypoint: rebuild the job from *spec* and run the unchanged `run_job`.

    Builds a minimal worker-side `ServeState` (its own working dir, real subprocess/simctl, and a
    fresh in-memory LogBus) — `popen`/`simctl`/`cwd` are injectable so the run can be driven with a
    fake subprocess on the gate. Returns the finished `Job`."""
    state = ServeState(runs_dir=Path("runs"), cwd=cwd or Path.cwd(), popen=popen, simctl=simctl)
    job = Job(
        id=str(spec["job_id"]),  # keep the control plane's id so logs/results line up
        cmd=list(spec["cmd"]),
        udids=list(spec.get("udids") or []),
        app_path=spec.get("app_path"),
        build=spec.get("build"),
        bus=state.logbus,
    )
    run_job(state, job)
    return job

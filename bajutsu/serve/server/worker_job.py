"""Serialize a job for the queue, and run it on the worker (BE-0015 server phase).

`run_job` (the boot → build → run → stream body) is unchanged between local and server hosting.
The only difference is *where* it runs: locally on a thread, on the server on a remote worker that
leased the job from the queue. `job_spec` captures the JSON-serializable fields a worker needs to
reconstruct the job; `execute_job_spec` is the worker entrypoint that rebuilds it and runs it.

The worker publishes the job's log to a **Redis** LogBus (built from the worker's redis URL), so a
control-plane replica streaming the same job id over its own `RedisLogBus` replays the log
cross-process — not into a private in-memory bus only the worker can see. `redis` is imported
lazily (inside `_redis_log_bus`), so this module stays safe to import without the ``worker`` extra.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from bajutsu import env
from bajutsu.serve.jobs import Job, ServeState, run_job
from bajutsu.serve.logbus import LogBus
from bajutsu.serve.server.object_store import (
    ObjectStore,
    artifact_prefix,
    object_store_from_env,
    s3_prefix,
)

# The worker's Redis URL, set in-process by `bajutsu worker` (see `set_broker_url`). Kept off the
# environment so a credential-bearing URL never propagates into the `bajutsu run` subprocesses
# `run_job` spawns (their env inherits os.environ via `_spawn_env`).
_broker_url: str | None = None


def set_broker_url(url: str) -> None:
    """Record the worker's Redis URL in-process so the queued `execute_job_spec` (which RQ runs in
    this worker, or a fork of it) discovers the broker — without exporting it to the environment."""
    global _broker_url
    _broker_url = url


def redis_url() -> str:
    """The worker's Redis URL: the in-process value `bajutsu worker` set, else the environment, else
    a localhost default — the same resolution `bajutsu worker` uses, so the log bus the worker
    publishes to is the broker the control plane reads."""
    return (
        _broker_url
        or os.environ.get("BAJUTSU_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or "redis://localhost:6379"
    )


def _redis_log_bus() -> LogBus:
    """A `RedisLogBus` over the worker's Redis (redis imported lazily — the ``worker`` extra)."""
    from redis import Redis

    from bajutsu.serve.server.logbus import RedisLogBus

    client: Any = Redis.from_url(redis_url())  # the real client is wider than the RedisLike slice
    return RedisLogBus(client)


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
        "materials": dict(job.materials),
    }


def _materialize(work: Path, materials: dict[str, str]) -> None:
    """Write each ``relpath -> content`` into the workspace before the run. Each path is confined to
    *work* (control-plane-built, but resolved defensively), so the run never writes outside it."""
    base = work.resolve()
    for rel, content in materials.items():
        dest = (work / rel).resolve()
        # Must be a file strictly under the workspace: reject an escaping path and the workspace
        # root itself (rel "" / "." / "scenarios/.."), which would write_text() a directory.
        if base not in dest.parents:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _upload_runs(work: Path, store: ObjectStore, prefix: str) -> None:
    """Upload the run tree the subprocess wrote (``work/runs/**``) to object storage, keyed by its
    path relative to ``work/runs`` under *prefix* — the exact keys `ObjectStorageArtifactStore`
    serves from. The control plane has no shared filesystem with the worker, so this is how a run's
    report / screenshots / video reach it."""
    runs = work / "runs"
    if not runs.is_dir():
        return
    for path in sorted(runs.rglob("*")):
        if path.is_file():
            rel = path.relative_to(runs).as_posix()
            store.put_bytes(f"{prefix}{rel}", path.read_bytes())


def execute_job_spec(
    spec: dict[str, Any],
    *,
    popen: Any = subprocess.Popen,
    simctl: env.RunFn = env._real_run,
    cwd: Path | None = None,
    bus: LogBus | None = None,
    store: ObjectStore | None = None,
) -> Job:
    """Worker entrypoint: rebuild the job from *spec*, run the unchanged `run_job`, then upload its
    run tree to object storage.

    Builds a minimal worker-side `ServeState` (its own working dir, real subprocess/simctl) whose
    LogBus is the shared Redis bus, so the streamed log reaches the control plane. After the run, the
    run tree is uploaded so the control plane's artifact store can serve it. `popen` / `simctl` /
    `cwd` / `bus` / `store` are injectable so the run can be driven with a fake subprocess, an
    in-memory Redis, and a fake object store on the gate. Returns the finished `Job`."""
    # The subprocess runs with cwd=work and writes runs/<id>/ relative to it, so runs_dir must be
    # work/runs — otherwise state.artifacts would be confined to an unrelated process-CWD runs/.
    work = cwd or Path.cwd()
    # Write the scenario + config the control plane shipped into the workspace, so the run's
    # workspace-relative `--scenario` / `--config` resolve here (the worker has no project on disk).
    _materialize(work, spec.get("materials") or {})
    log_bus = bus if bus is not None else _redis_log_bus()
    state = ServeState(runs_dir=work / "runs", cwd=work, popen=popen, simctl=simctl, logbus=log_bus)
    job = Job(
        id=str(spec["job_id"]),  # keep the control plane's id so logs/results line up
        cmd=list(spec["cmd"]),
        udids=list(spec.get("udids") or []),
        app_path=spec.get("app_path"),
        build=spec.get("build"),
        bus=state.logbus,
    )
    run_job(state, job)
    # Upload the run tree so the control plane can serve it. Skip when no object store is configured
    # (e.g. a worker without one) rather than failing the finished run.
    uploader = store if store is not None else object_store_from_env()
    if uploader is not None:
        _upload_runs(work, uploader, artifact_prefix(s3_prefix()))
    return job

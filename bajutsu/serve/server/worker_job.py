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
import shutil
import subprocess
from pathlib import Path
from typing import Any

from bajutsu import env
from bajutsu.config import DEFAULT_ORG as _DEFAULT_ORG
from bajutsu.serve.jobs import Job, ServeState, run_job
from bajutsu.serve.logbus import LogBus
from bajutsu.serve.server.object_store import (
    ObjectStore,
    artifact_prefix,
    object_store_from_env,
    org_prefix,
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
        # record: where in the workspace the authored file lands + (app, ref) to persist it as.
        "out_path": job.out_path,
        "record_save": list(job.record_save) if job.record_save else None,
        # run: download visual baselines into the workspace before running.
        "materialize_baselines": job.materialize_baselines,
        # The run's org, so the worker reads/writes this org's object-store prefix (BE-0015).
        "org": job.org,
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


def _upload_runs(work: Path, store: ObjectStore, prefix: str, run_id: str) -> None:
    """Upload only this job's run tree (``work/runs/<run_id>/**``) to object storage, keyed by its
    path relative to ``work/runs`` under *prefix* — the exact keys `ObjectStorageArtifactStore`
    serves. Scoped to *run_id* because a worker's CWD is shared across jobs (don't re-upload old
    runs); symlinks are skipped and resolved paths must stay under the run dir (no exfiltration).
    Files stream from disk (`put_file`), so a large video doesn't load into memory."""
    runs = work / "runs"
    run_dir = runs / run_id
    if not run_dir.is_dir():
        return
    base = run_dir.resolve()
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue  # skip symlinks (and non-files) so nothing outside the run dir is uploaded
        if base != path.resolve().parent and base not in path.resolve().parents:
            continue  # defensive: stay under the run dir
        rel = path.relative_to(runs).as_posix()  # "<run_id>/..."
        store.put_file(f"{prefix}{rel}", path)


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
    # The run's org keys every object-store path, so the worker reads/writes the same prefix the
    # control plane serves from (BE-0015 multi-tenancy). The default org keeps the base prefix.
    base = org_prefix(s3_prefix(), str(spec.get("org") or _DEFAULT_ORG))
    # Write the scenario + config the control plane shipped into the workspace, so the run's
    # workspace-relative `--scenario` / `--config` resolve here (the worker has no project on disk).
    _materialize(work, spec.get("materials") or {})
    log_bus = bus if bus is not None else _redis_log_bus()
    # Download the visual baselines into the workspace before the run (the cmd's `--baselines` points
    # at this dir); the control plane's baselines live in object storage the worker can't share.
    if spec.get("materialize_baselines"):
        baseline_src = store if store is not None else object_store_from_env()
        if baseline_src is not None:
            _materialize_baselines(work, baseline_src, prefix=base)
    state = ServeState(runs_dir=work / "runs", cwd=work, popen=popen, simctl=simctl, logbus=log_bus)
    job = Job(
        id=str(spec["job_id"]),  # keep the control plane's id so logs/results line up
        cmd=list(spec["cmd"]),
        udids=list(spec.get("udids") or []),
        app_path=spec.get("app_path"),
        build=spec.get("build"),
        out_path=spec.get("out_path"),  # so the terminal-status payload reports it (record jobs)
        bus=state.logbus,
    )
    run_job(state, job)
    uploader = store if store is not None else object_store_from_env()
    if uploader is not None:
        # A `run` produced a run tree: upload it (scoped to this run id — the worker's CWD is shared
        # across jobs). A `record` authored a scenario: persist it to per-project storage.
        if job.run_id:
            _upload_runs(work, uploader, artifact_prefix(base), job.run_id)
        save = spec.get("record_save")
        # Validate the queue payload's shape before indexing — a malformed spec must not crash here.
        if isinstance(save, (list, tuple)) and len(save) == 2 and job.out_path:
            _save_authored(work, uploader, job.out_path, str(save[0]), str(save[1]), prefix=base)
    return job


def _materialize_baselines(work: Path, store: ObjectStore, *, prefix: str = "") -> None:
    """Download every visual baseline into ``work/baselines/`` before the run (the cmd's
    ``--baselines`` points here), via the same `ObjectBaselineStore` keys the control plane writes
    under the run's org *prefix*."""
    from bajutsu.serve.server.baselines import ObjectBaselineStore

    baselines = work / "baselines"
    # The workspace is reused across jobs, so clear first — otherwise a baseline deleted/renamed in
    # storage would linger and skew the comparison.
    if baselines.exists():
        shutil.rmtree(baselines, ignore_errors=True)
    src = ObjectBaselineStore(store, prefix=prefix)
    base = baselines.resolve()
    for name in src.names():
        data = src.open_bytes(name)
        if data is None:
            continue
        dest = (baselines / name).resolve()
        if base not in dest.parents:
            continue  # defensive: stay strictly under the baselines dir
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _save_authored(
    work: Path, store: ObjectStore, out_path: str, app: str, ref: str, *, prefix: str = ""
) -> None:
    """Persist the scenario a `record` run wrote at *out_path* in the workspace to per-project
    storage as ``(app, ref)`` — via the same `ObjectScenarioStorage` keys the control plane reads
    under the run's org *prefix*."""
    from bajutsu.serve.server.scenarios import ObjectScenarioStorage

    src = (work / out_path).resolve()
    # Confine to the workspace: a crafted spec with an absolute / ``..`` out_path must not read &
    # upload arbitrary host files (mirrors _materialize / _upload_runs).
    if work.resolve() not in src.parents or not src.is_file():
        return
    # `list` is the apps provider (returns []); save() doesn't consult it, only the key scheme.
    ObjectScenarioStorage(store, list, prefix=prefix).save(
        app, ref, src.read_text(encoding="utf-8")
    )

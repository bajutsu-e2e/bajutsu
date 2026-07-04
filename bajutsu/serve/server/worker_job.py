"""Serialize a job for the queue, and run it on the worker (BE-0015 / BE-0106).

`run_job` (the boot → build → run → stream body) is unchanged between local and server hosting.
The only difference is *where* it runs: locally on a thread, on the server on a remote worker that
leased the job over HTTP. `job_spec` captures the JSON-serializable fields a worker needs to
reconstruct the job; `execute_job_spec` is the worker entrypoint that rebuilds it and runs it.

The worker buffers the job's log in an `InMemoryLogBus`; the caller (the `bajutsu worker` HTTP
loop) writes it to `console.log` and uploads it with the run tree.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from bajutsu import env
from bajutsu.config import DEFAULT_ORG as _DEFAULT_ORG
from bajutsu.serve import oplog
from bajutsu.serve.jobs import Job, ServeState, run_job
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.server.object_store import (
    ObjectStore,
    artifact_prefix,
    object_store_from_env,
    org_prefix,
    s3_prefix,
)


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
        # Who started the run, so the worker can attribute the recorded run to the user (BE-0015).
        "actor": job.actor,
        # Per-run evidence-upload prefix (BE-0110): the worker relays it when requesting presigned
        # PUT URLs, so the run's evidence lands under the lifecycle path CI chose.
        "evidence_prefix": job.evidence_prefix,
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


def _repository_from_env_or_none() -> Any:
    """The system of record from `BAJUTSU_DATABASE_URL`, or None when the worker has no database
    configured — or no `db` extra installed (a worker may run without it). Lazy + guarded so the
    worker imports cleanly without SQLAlchemy."""
    try:
        from bajutsu.serve.server.db import repository_from_env
    except ImportError:
        return None
    return repository_from_env()


def execute_job_spec(
    spec: dict[str, Any],
    *,
    popen: Any = subprocess.Popen,
    simctl: env.RunFn = env._real_run,
    cwd: Path | None = None,
    bus: LogBus | None = None,
    store: ObjectStore | None = None,
    repository: Any = None,
) -> Job:
    """Worker entrypoint: rebuild the job from *spec*, run the unchanged `run_job`, then upload its
    run tree to object storage.

    Builds a minimal worker-side `ServeState` (its own working dir, real subprocess/simctl) whose
    LogBus is the shared Redis bus, so the streamed log reaches the control plane. After the run, the
    run tree is uploaded so the control plane's artifact store can serve it. With a *repository*
    (the worker's `BAJUTSU_DATABASE_URL`), the finished run is recorded into the system of record
    under its org/actor — the run executes here, not on the control plane, so this is where it's
    recorded (BE-0015). `popen` / `simctl` / `cwd` / `bus` / `store` / `repository` are injectable so
    the run can be driven with a fake subprocess, an in-memory Redis, a fake object store, and an
    in-memory database on the gate. Returns the finished `Job`."""
    # The subprocess runs with cwd=work and writes runs/<id>/ relative to it, so runs_dir must be
    # work/runs — otherwise state.artifacts would be confined to an unrelated process-CWD runs/.
    work = cwd or Path.cwd()
    org = str(spec.get("org") or _DEFAULT_ORG)
    # The run's org keys every object-store path, so the worker reads/writes the same prefix the
    # control plane serves from (BE-0015 multi-tenancy). The default org keeps the base prefix.
    base = org_prefix(s3_prefix(), org)
    # Write the scenario + config the control plane shipped into the workspace, so the run's
    # workspace-relative `--scenario` / `--config` resolve here (the worker has no project on disk).
    _materialize(work, spec.get("materials") or {})
    log_bus = bus if bus is not None else InMemoryLogBus()
    # Download the visual baselines into the workspace before the run (the cmd's `--baselines` points
    # at this dir); the control plane's baselines live in object storage the worker can't share.
    if spec.get("materialize_baselines"):
        baseline_src = store if store is not None else object_store_from_env()
        if baseline_src is not None:
            _materialize_baselines(work, baseline_src, prefix=base)
    # A repository (worker BAJUTSU_DATABASE_URL) lets run_job's `_persist_run` record the finished
    # run under its org — the run executes here, so this is the only place it can be recorded.
    state = ServeState(
        runs_dir=work / "runs",
        cwd=work,
        popen=popen,
        simctl=simctl,
        logbus=log_bus,
        repository=repository if repository is not None else _repository_from_env_or_none(),
    )
    job = Job(
        id=str(spec["job_id"]),  # keep the control plane's id so logs/results line up
        cmd=list(spec["cmd"]),
        udids=list(spec.get("udids") or []),
        app_path=spec.get("app_path"),
        build=spec.get("build"),
        out_path=spec.get("out_path"),  # so the terminal-status payload reports it (record jobs)
        bus=state.logbus,
        # Carry org + actor so the recorded run is attributed correctly (BE-0015).
        org=org,
        actor=spec.get("actor"),
    )
    # Bind the job's ids so every operational record on this worker correlates to it (BE-0055);
    # `run_id` is the run's own id, minted by `run_job`, so it binds only once the run has started.
    with oplog.job_context(job_id=job.id, org=org, actor=spec.get("actor")):
        oplog.log_event(_logger, "worker.job.started", "worker started job")
        run_job(state, job)
        # Bind run_id for correlation only: the run resolves `${secrets.X}` inside the `bajutsu run`
        # subprocess, so those values aren't in this process to seed a run-scoped redactor here.
        with oplog.run_context(job.run_id) if job.run_id else nullcontext():
            oplog.log_event(_logger, "worker.job.finished", "worker finished job")
            uploader = store if store is not None else object_store_from_env()
            if uploader is not None:
                _upload_outputs(work, uploader, base, job, spec)
    return job


_logger = logging.getLogger("bajutsu.serve.worker")


def _upload_outputs(
    work: Path, uploader: ObjectStore, base: str, job: Job, spec: dict[str, Any]
) -> None:
    """Upload the finished job's outputs; surface (never swallow) an upload failure.

    A `run` produced a run tree: upload it (scoped to this run id — the worker's CWD is shared
    across jobs). A `record` authored a scenario: persist it to per-project storage.
    """
    try:
        if job.run_id:
            _upload_runs(work, uploader, artifact_prefix(base), job.run_id)
        save = spec.get("record_save")
        # Validate the queue payload's shape before indexing — a malformed spec must not crash here.
        if isinstance(save, (list, tuple)) and len(save) == 2 and job.out_path:
            _save_authored(work, uploader, job.out_path, str(save[0]), str(save[1]), prefix=base)
    except Exception:
        oplog.log_event(
            _logger, "artifact.upload.failed", "uploading job outputs failed", level=logging.ERROR
        )
        raise


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

"""Serialize a job for the queue, and run it on the worker (BE-0015 / BE-0106).

`run_job` (the boot → build → run → stream body) is unchanged between local and server hosting.
The only difference is *where* it runs: locally on a thread, on the server on a remote worker that
leased the job over HTTP. `job_spec` captures the JSON-serializable fields a worker needs to
reconstruct the job; `execute_job_spec` is the worker entrypoint that rebuilds it and runs it.

The worker holds no cloud credentials (BE-0160): its object I/O — downloading baselines before the
run, uploading the run tree after, persisting a `record` job's authored scenario — is brokered by
the control plane's presigned URLs through the injected `WorkerIO` seam. The `bajutsu worker` HTTP
loop supplies the real (presigned-URL-backed) implementation; the gate injects a fake.
"""

from __future__ import annotations

import logging
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Protocol

from bajutsu import simctl as _simctl
from bajutsu.serve import oplog
from bajutsu.serve.jobs import run_job
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.orgs import DEFAULT_ORG as _DEFAULT_ORG
from bajutsu.serve.state import Job, ServeState


class WorkerIO(Protocol):
    """The worker's object I/O, brokered by the control plane's presigned URLs (BE-0160).

    The worker holds no cloud credentials — only an HTTP client — so each method reads/writes object
    storage over plain HTTP against a URL the control plane signed. Injectable so the gate drives a
    fake without a network or a cloud SDK.
    """

    def download_baselines(self, work: Path) -> None:
        """Download the run's visual baselines into ``work/baselines`` before the run."""

    def upload_run(self, work: Path, run_id: str) -> None:
        """Upload ``work/runs/<run_id>/**`` to the control plane's artifact store."""

    def save_scenario(self, work: Path, out_path: str, app: str, ref: str) -> None:
        """Persist a `record` job's authored scenario at *out_path* to storage as ``(app, ref)``."""


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
    simctl: _simctl.RunFn = _simctl._real_run,
    cwd: Path | None = None,
    bus: LogBus | None = None,
    io: WorkerIO | None = None,
    repository: Any = None,
) -> Job:
    """Worker entrypoint: rebuild the job from *spec*, run the unchanged `run_job`, then upload its
    run tree through the control plane's presigned URLs.

    Builds a minimal worker-side `ServeState` (its own working dir, real subprocess/simctl) whose
    LogBus is the shared Redis bus, so the streamed log reaches the control plane. Object I/O goes
    through *io* (the `WorkerIO` seam, BE-0160): baselines are downloaded before the run and the run
    tree uploaded after, all over presigned URLs — the worker holds no cloud credentials. With a
    *repository* (the worker's `BAJUTSU_DATABASE_URL`), the finished run is recorded into the system
    of record under its org/actor — the run executes here, not on the control plane, so this is where
    it's recorded (BE-0015). `popen` / `simctl` / `cwd` / `bus` / `io` / `repository` are injectable
    so the run can be driven with a fake subprocess, an in-memory Redis, a fake `WorkerIO`, and an
    in-memory database on the gate. Returns the finished `Job`."""
    # The subprocess runs with cwd=work and writes runs/<id>/ relative to it, so runs_dir must be
    # work/runs — otherwise state.artifacts would be confined to an unrelated process-CWD runs/.
    work = cwd or Path.cwd()
    org = str(spec.get("org") or _DEFAULT_ORG)
    # Write the scenario + config the control plane shipped into the workspace, so the run's
    # workspace-relative `--scenario` / `--config` resolve here (the worker has no project on disk).
    _materialize(work, spec.get("materials") or {})
    log_bus = bus if bus is not None else InMemoryLogBus()
    # Download the visual baselines into the workspace before the run (the cmd's `--baselines` points
    # at this dir), over the presigned GET URLs the control plane signed — no worker credentials.
    if spec.get("materialize_baselines") and io is not None:
        io.download_baselines(work)
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
            if io is not None:
                _upload_outputs(work, io, job, spec)
    return job


_logger = logging.getLogger("bajutsu.serve.worker")


def _upload_outputs(work: Path, io: WorkerIO, job: Job, spec: dict[str, Any]) -> None:
    """Upload the finished job's outputs through the presigned-URL seam; surface (never swallow) an
    upload failure — a report the control plane can't serve is a failure, not a silent skip.

    A `run` produced a run tree: upload it (scoped to this run id — the worker's CWD is shared across
    jobs). A `record` authored a scenario: persist it to per-project storage.
    """
    try:
        if job.run_id:
            io.upload_run(work, job.run_id)
        save = spec.get("record_save")
        # Validate the queue payload's shape before indexing — a malformed spec must not crash here.
        if isinstance(save, (list, tuple)) and len(save) == 2 and job.out_path:
            io.save_scenario(work, job.out_path, str(save[0]), str(save[1]))
    except Exception:
        oplog.log_event(
            _logger, "artifact.upload.failed", "uploading job outputs failed", level=logging.ERROR
        )
        raise

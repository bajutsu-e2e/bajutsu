"""Job execution engine: spawning, cancellation, device boot, and app build.

The serve state container (`ServeState`, `Job`, and the value types) lives in `serve/state.py`
(BE-0206); this module holds only the run/cancel lifecycle that mutates a `Job`. The dependency is
one-directional: the functions here read `ServeState` and mutate `Job`, while nothing in the state
half calls back into execution.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from bajutsu import simctl as _simctl
from bajutsu.handoff import REQUEST_LINE_PREFIX as _HANDOFF_REQUEST_PREFIX
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.state import Job, ServeState

logger = logging.getLogger(__name__)

# The run command prints "PASS/FAIL  runs/<id>/manifest.json"; pull <id> from it.
_RUN_ID_RE = re.compile(r"runs/([0-9A-Za-z._-]+)/manifest\.json")


def _spawn_env() -> dict[str, str]:
    """The child env for a spawned run/record: the venv bin dir (where the ``idb`` client lives)
    on PATH.  Inherits the serve process's environment, so an ``ANTHROPIC_API_KEY`` set from the
    WebUI (which writes only into ``os.environ``) is carried through to the job."""
    e = dict(os.environ)
    bindir = str(Path(sys.executable).parent)
    e["PATH"] = bindir + os.pathsep + e.get("PATH", "")
    return e


def _log(job: Job, line: str) -> None:
    with job.lock:
        job.lines.append(line)
    if job.bus is not None:  # publish outside job.lock; the bus has its own lock
        job.bus.publish(job.id, line)


def _terminate(proc: Any) -> None:
    """Best-effort stop of a live subprocess AND its children; ignore an already-exited / fake proc.

    A record job spawns its own children (the authoring agent shells out to `claude -p`), so
    terminating only the top process orphans them. The job is launched in its own session
    (`start_new_session`), so signal the whole process group; fall back to terminating just the
    process (a fake proc in tests, or a platform without process groups)."""
    with contextlib.suppress(OSError, ProcessLookupError, AttributeError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        return
    with contextlib.suppress(OSError, ProcessLookupError, AttributeError):
        proc.terminate()


def _register_proc(job: Job, proc: Any) -> bool:
    """Attach *proc* as the job's live subprocess so a cancel request can reach it.  If a cancel
    already arrived, kill *proc* at once and return False so the caller stops before streaming."""
    with job.lock:
        if job.cancelled:
            kill = True
        else:
            job.proc = proc
            kill = False
    if kill:
        _terminate(proc)
    return not kill


def cancel_job(job: Job) -> bool:
    """Request cancellation of a running job: flag it and terminate its current subprocess (the
    streamed output then ends and run_job marks the job done).  Returns False if already
    finished."""
    with job.lock:
        if job.status == "done":
            return False
        job.cancelled = True
        proc = job.proc
        noted = not job.lines or job.lines[-1] != "cancelled"
        if noted:
            job.lines.append("cancelled")
    if noted and job.bus is not None:
        job.bus.publish(job.id, "cancelled")
    if proc is not None:
        _terminate(proc)
    return True


def send_response(job: Job, line: str) -> bool:
    """Write a human-handoff response line to the job's stdin, resuming a paused `record` (BE-0179).

    Clears the awaiting-human state and returns False if the job has no live stdin (already
    finished, or not a handoff-capable spawn) so the caller can report the resume never landed.
    """
    with job.lock:
        proc = job.proc
        job.awaiting_human = False
    stdin = getattr(proc, "stdin", None)
    if stdin is None:
        return False
    try:
        stdin.write(line if line.endswith("\n") else line + "\n")
        stdin.flush()
    except (OSError, ValueError):
        return False
    return True


def _boot_devices(state: ServeState, job: Job) -> bool:
    """Boot the job's devices in parallel (each ``bootstatus -b`` boots its device and waits
    until ready) so multiple cold simulators come up at the same time, then the run drives
    them concurrently.  Returns False and marks the job failed if any device won't boot."""
    if not job.udids:
        return True
    for udid in job.udids:
        _log(job, f"booting {udid}…")
    errors: dict[str, str] = {}
    errlock = threading.Lock()

    def boot(udid: str) -> None:
        try:
            state.simctl(_simctl.bootstatus_cmd(udid), None)
            _log(job, f"booted {udid}")
        except (OSError, subprocess.CalledProcessError) as e:
            with errlock:
                errors[udid] = str(e)

    threads = [threading.Thread(target=boot, args=(u,), daemon=True) for u in job.udids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        for udid, msg in errors.items():
            _log(job, f"boot failed: {udid}: {msg}")
        with job.lock:
            job.exit_code = 1
            job.status = "done"
        return False
    return True


def _build_app(state: ServeState, job: Job) -> bool:
    """Build the app's binary on demand when it is missing.  Returns True if the run may
    proceed: nothing to build (no ``build`` command, no ``app_path``, or the binary already
    exists), or the build command succeeded.  Returns False (marking the job failed) only when
    a needed build fails — so the run isn't spawned against a missing binary."""
    if not job.build or not job.app_path:
        return True
    cwd = job.cwd or state.cwd
    if (cwd / job.app_path).exists():
        return True
    _log(job, f"app binary missing ({job.app_path}) — building: {job.build}")
    try:
        proc = state.popen(
            shlex.split(job.build),
            cwd=str(cwd),
            env=_spawn_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,  # own process group, so a cancel can stop its children too
        )
        if not _register_proc(job, proc):
            proc.wait()
            with job.lock:
                job.exit_code, job.status, job.proc = proc.returncode or 1, "done", None
            return False
        try:
            for raw in proc.stdout or []:
                _log(job, raw.rstrip("\n"))
        except OSError:
            _terminate(proc)
        proc.wait()
        code = proc.returncode
    except OSError as e:
        _log(job, f"build failed: {e}")
        code = 1
    if code != 0:
        _log(job, f"build failed (exit {code}) — skipping the run")
        with job.lock:
            job.exit_code = code
            job.status = "done"
        return False
    _log(job, "build ok")
    return True


def run_job(state: ServeState, job: Job) -> None:
    """Boot the job's devices (if any), build the app if its binary is missing, then run
    ``job.cmd``, capturing combined output line-by-line and the produced run id. The job's live
    log channel is closed on every exit path, so an ``/events`` subscriber's stream always ends."""
    try:
        _run_job(state, job)
    finally:
        _record_provenance(state, job)
        _persist_run(state, job)
        if job.bus is not None:  # run_job returning means the job finished — end the live stream
            # Record the terminal status on the bus so a control-plane replica reading a
            # worker-run job sees the real exit/run id (its own Job stays "running") (BE-0015 W2).
            # Exclude the log buffer — the lines already live in the bus's stream, so duplicating
            # them into the done payload would needlessly bloat it (Redis memory).
            job.bus.close(job.id, json.dumps(job.view(include_lines=False)))


def _record_provenance(state: ServeState, job: Job) -> None:
    """Record an uploaded bundle's provenance into its run's manifest.json (BE-0073). The run
    subprocess owns the manifest; serve, which alone knows the upload's filename + zip sha256, adds
    a `provenance` block afterward so "what did this run execute?" is answerable (DESIGN §2). A
    no-op for a normal run (`provenance` unset) or one that produced no run id (a build/boot
    failure). Best-effort: a failure here is logged, never raised — it must not strand job
    finalization (this runs in run_job's `finally`)."""
    if job.provenance is None or job.run_id is None or not valid_run_id(job.run_id):
        return
    # The run wrote into the --runs-dir we passed (serve's store under base_cwd, since the run's cwd
    # is the bundle root); the run id is a single safe segment (checked above), so this can't escape
    # that tree. Resolve to match the absolute --runs-dir the subprocess was given.
    manifest = (state.base_cwd / state.runs_dir / job.run_id / "manifest.json").resolve()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        # Merge, don't overwrite: the run subprocess already wrote a `provenance` block (scenario
        # fingerprint, and BE-0090's `uploadExec` decision). serve adds the upload identity it alone
        # knows; clobbering would drop both of the subprocess's records.
        existing = data.get("provenance")
        existing = existing if isinstance(existing, dict) else {}
        data["provenance"] = {**existing, **job.provenance}
        # Write atomically (temp + replace): the report viewer / list_runs may read the manifest
        # concurrently, and a plain write_text truncates first — a reader could catch it empty.
        tmp = manifest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(manifest)
    except (OSError, ValueError):
        logger.warning("failed to record bundle provenance into %s", manifest, exc_info=True)


def _persist_run(state: ServeState, job: Job) -> None:
    """Record a finished `run` into the system of record so the history list survives independently
    of the artifact store and is org-scoped (BE-0015), and label it with the active project (BE-0225).
    A no-op only for a job that produced no run id (record/crawl, or a build/boot failure). With a
    repository the run is recorded under its actor's org (the single `default` org for a token/CI run
    or an unknown user) so it shows in that org's history; without one (local / stdlib serve) there is
    no history table, so the local registry instead tags the run into its project→run-ids index — the
    stand-in for the `runs.project_id` column — when a project is active.

    Persistence must never break job finalization: this runs inside `run_job`'s `finally`, just
    before the live-log stream is closed, so any error (a missing org/user row, an FK violation on
    Postgres, a flaky DB) is caught and logged rather than stranding the stream."""
    if job.run_id is None:
        return
    run_id = job.run_id
    org = job.org
    # The project that owns the run so a per-project listing (BE-0225) and the cross-project dashboard
    # (BE-0226) can partition it. Prefer the id `start_run` resolved at enqueue and carried on the job
    # (also the only source on a remote worker, whose state has no registry); fall back to resolving
    # the active project here for a job built without going through the enqueue path. None when no hub
    # is wired or no project is active, leaving the run unlabeled exactly as before.
    registry = state.project_registry
    project_id: str | None = job.project_id
    if project_id is None and registry is not None:
        try:
            active = registry.resolve_active(org_id=org)
            project_id = active.id if active is not None else None
        except Exception:
            # Resolving the active project reaches the registry backend (a database for
            # `SqlProjectRegistry`), so it can fail like the persistence write below — and this runs in
            # `run_job`'s `finally`, so an escape would strand the live-log stream (the docstring's
            # contract). A failure leaves the run unlabeled, exactly as when no hub is wired.
            logger.warning("failed to resolve the active project for run %s", run_id, exc_info=True)
    if state.repository is None:
        # Local / stdlib serve: no system of record, so the local registry keeps the project→run-ids
        # index (the stand-in for the runs.project_id column). Guarded like the DB path so a registry
        # error can never break job finalization.
        if registry is not None and project_id is not None:
            try:
                registry.tag_run(org_id=org, project_id=project_id, run_id=run_id)
            except Exception:
                logger.warning("failed to tag run %s to its project", run_id, exc_info=True)
        return
    repo = state.repository
    try:
        # Lazy import: only a server backend has a repository, where SQLAlchemy is already loaded,
        # so the default serve path never pulls server.db in (the import guard stays green).
        from bajutsu.serve.server.db import RunRecord

        ok = job.exit_code == 0 and not job.cancelled
        # The run's org was decided at job creation (and travels to a worker in the spec). Attribute
        # `created_by` only to a user that actually exists, so the foreign key can't fail (a token /
        # CI run has no actor; an OAuth run's user was upserted at login).
        repo.ensure_org(org, slug=org, name=org)
        created_by = job.actor if job.actor and repo.user_org(job.actor) is not None else None
        # Read + parse the manifest once and feed both the summary and the provenance stamp: a hosted
        # `open_bytes` can be an object-storage round trip, so a second read per run would double the
        # cost `_run_summary` was written to avoid.
        manifest = _read_manifest(state, run_id)
        scenario_hash, tool_version, git_revision = _run_provenance(manifest)
        repo.record_run(
            RunRecord(
                id=run_id,
                org_id=org,
                status="done",
                project_id=project_id,
                created_by=created_by,
                ok=ok,
                summary=_run_summary(run_id, manifest, ok=ok),
                scenario_hash=scenario_hash,
                tool_version=tool_version,
                git_revision=git_revision,
            )
        )
    except Exception:
        logger.warning("failed to persist run %s to the system of record", run_id, exc_info=True)


def _read_manifest(state: ServeState, run_id: str) -> dict[str, Any] | None:
    """Parse a run's `manifest.json` once, or None if it's missing, unreadable, or not a JSON object
    (a corrupted/partial write left a bare list/string/`null`). `_persist_run` reads it a single time
    and hands the parsed value to both the summary and the provenance stamp, since a hosted
    `open_bytes` can be a real object-storage round trip."""
    raw = state.artifacts.open_bytes(f"{run_id}/manifest.json")
    if raw is None:
        return None
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _run_provenance(manifest: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    """The run's identity stamp — (scenarioHash, toolVersion, gitRevision) — from its `manifest.json`
    provenance block (BE-0049), mirrored onto the DB record so cross-run flakiness groups by scenario
    identity straight from the DB (BE-0220). All None for a pre-provenance run or an unreadable /
    malformed manifest — ungroupable, never blocking (mirrors audit --history's `skipped`)."""
    prov = manifest.get("provenance") if manifest is not None else None
    if not isinstance(prov, dict):
        return None, None, None

    def _str(key: str) -> str | None:
        value = prov.get(key)
        return value if isinstance(value, str) else None

    return _str("scenarioHash"), _str("toolVersion"), _str("gitRevision")


def _run_summary(run_id: str, manifest: dict[str, Any] | None, *, ok: bool) -> dict[str, Any]:
    """The run's history-list summary, from just this run's parsed `manifest.json` (not a full
    `list_runs()` scan, which re-reads every run's manifest from object storage). `write_report`
    writes `report.html` alongside the manifest, so a readable manifest means the report exists."""
    if manifest is None:
        return {"id": run_id, "ok": ok, "report": False, "scenarios": [], "passed": 0, "total": 0}
    scenarios = [s for s in (manifest.get("scenarios") or []) if isinstance(s, dict)]
    return {
        "id": run_id,
        "ok": bool(manifest.get("ok")),
        "report": True,
        "scenarios": [str(s.get("scenario", "")) for s in scenarios],
        "passed": sum(1 for s in scenarios if s.get("ok")),
        "total": len(scenarios),
    }


def _run_job(state: ServeState, job: Job) -> None:
    if not _boot_devices(state, job):
        return
    if not _build_app(state, job):
        return
    # A stdin pipe is the human-handoff response channel (BE-0179): a paused `record --handoff stream`
    # reads the human's response line here. Only handoff-capable commands get the pipe; every other
    # job gets DEVNULL, so a subprocess that unexpectedly reads stdin sees EOF rather than blocking
    # forever on input that will never arrive.
    stdin = subprocess.PIPE if "--handoff" in job.cmd else subprocess.DEVNULL
    proc = state.popen(
        job.cmd,
        cwd=str(job.cwd or state.cwd),
        env=_spawn_env(),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,  # own process group, so a cancel stops its children (e.g. `claude -p`)
    )
    if not _register_proc(job, proc):
        proc.wait()
        with job.lock:
            job.exit_code, job.status, job.proc = proc.returncode or 1, "done", None
        return
    try:
        for raw in proc.stdout or []:
            line = raw.rstrip("\n")
            if line.startswith(_HANDOFF_REQUEST_PREFIX):
                # A handoff request (BE-0179): mark the job awaiting-human and relay the line to the
                # bus (where the SSE layer turns it into a `human-request` event). Kept out of
                # `job.lines` so the transcript view isn't polluted by the serialized payload.
                with job.lock:
                    job.awaiting_human = True
                if job.bus is not None:
                    job.bus.publish(job.id, line)
                continue
            match = _RUN_ID_RE.search(line)
            with job.lock:
                job.lines.append(line)
                if match:
                    job.run_id = match.group(1)
            if job.bus is not None:
                job.bus.publish(job.id, line)
    except OSError:
        _terminate(proc)
    proc.wait()
    with job.lock:
        job.proc = None
        job.exit_code = proc.returncode
        job.status = "done"
        # A record that paused for a human but ended without a response (a StreamHandoff timeout →
        # cancel, or a killed job) must not report awaiting-human on its terminal view — the process
        # is gone and cannot be resumed (BE-0179).
        job.awaiting_human = False

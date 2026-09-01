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
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bajutsu import device_os
from bajutsu import simctl as _simctl
from bajutsu.agents.ai_config import PROVIDER_MANAGED_ENV
from bajutsu.cancellation import GRACE_ENV, grace_seconds
from bajutsu.evidence.redaction import Redactor
from bajutsu.evidence.sink import RunArtifactWriter
from bajutsu.handoff import REQUEST_LINE_PREFIX as _HANDOFF_REQUEST_PREFIX
from bajutsu.run_files import RunArtifactReader
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.state import Job, ServeState

if TYPE_CHECKING:
    from bajutsu.serve.server.db import Repository

logger = logging.getLogger(__name__)

# The run command prints "PASS/FAIL  runs/<id>/manifest.json"; pull <id> from it.
_RUN_ID_RE = re.compile(r"runs/([0-9A-Za-z._-]+)/manifest\.json")


def _spawn_env(job: Job) -> dict[str, str]:
    """The child env for a spawned run/record: the venv bin dir (where the ``idb`` client lives) on
    PATH, plus *job*'s per-org AI provider overlay (BE-0229).

    Inherits the serve process's environment, so an ``ANTHROPIC_API_KEY`` set from the Web UI (which
    the local secret store writes into ``os.environ``, BE-0136) is carried through to the job. On top
    of that, the job's `env_overlay` carries the requesting org's provider/model/effort/language, so
    the spawn uses *that* org's selection without the serve process ever mutating its shared
    ``os.environ`` — the tenant-isolation guarantee. When the overlay names a provider it is
    authoritative, so the Bajutsu-managed provider vars are cleared from the inherited env first and
    then replaced, rather than letting a stale launch-env value leak through; an empty overlay (no
    selection) leaves the inherited env untouched, preserving the zero-config path (BE-0101)."""
    e = dict(os.environ)
    if job.env_overlay:
        for var in PROVIDER_MANAGED_ENV:
            e.pop(var, None)
        e.update(job.env_overlay)
    bindir = str(Path(sys.executable).parent)
    e["PATH"] = bindir + os.pathsep + e.get("PATH", "")
    if job.graceful_cancel:
        # Tell the spawned run how long it has to close itself out, so the deadline its own SIGTERM
        # handler enforces is bound to *this* grace window (BE-0370). An independently chosen constant
        # could be the shorter of the two and kill the run before `_assemble_report` wrote a manifest,
        # reproducing the silent gap for every ordinary cancel — `serve`'s longer window could never
        # rescue a run that already killed itself.
        e[GRACE_ENV] = f"{grace_seconds():g}"
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


def _pgid_of(proc: Any) -> int | None:
    """The process group of a live spawned job, or None when it can't be read (a proc double).

    Read while the leader is still alive: once it has exited and been reaped its pid — which is also
    its pgid, since every job is spawned with `start_new_session` — can be reused, so the post-exit
    sweep below must never re-derive the group it is about to signal.
    """
    try:
        return os.getpgid(proc.pid)
    except (OSError, AttributeError):
        return None


def _sweep_group(pgid: int | None) -> None:
    """Reap whatever is left of a cooperatively-cancelled run's process group (BE-0370).

    The leader has exited — cleanly, or via the grace-period escalation — so its own end-of-scenario
    teardown has already had its chance to stop the backend driver it was actuating through. A driver
    child that outlived it would otherwise be left orphaned on the serve host, which is why the
    leader-only signal is still followed by the group-wide sweep it replaced. Best-effort: an already
    empty group is the normal case.
    """
    if pgid is None:
        return
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)


def _still_running(proc: Any) -> bool:
    """Whether *proc* has yet to exit; True for a proc double that cannot answer (never assume gone)."""
    poll = getattr(proc, "poll", None)
    return poll is None or poll() is None


def _kill(proc: Any) -> None:
    """SIGKILL a process and its group; never raises, and ignores an already-exited / fake proc.

    The unconditional end of the cancel escalation below. `_terminate`'s SIGTERM is what a driver
    child answers; only SIGKILL is what nothing can absorb.
    """
    with contextlib.suppress(OSError, ProcessLookupError, AttributeError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return
    with contextlib.suppress(OSError, ProcessLookupError, AttributeError):
        proc.kill()


# How long the escalation gives a job's process group to unwind on the group-wide SIGTERM before it
# resorts to SIGKILL — the same window, for the same reason, that the XCUITest runner's own teardown
# gives its group (`_terminate_process_group` in `platform_lifecycle/environments/xcuitest.py`): a
# child of `xcodebuild` can ignore a SIGTERM and keep holding the device.
_ESCALATION_UNWIND_SECONDS = 5.0


def _escalate(proc: Any) -> None:
    """End a cooperatively-cancelled run that outlived its grace window, and say so (BE-0370).

    A group-wide SIGTERM alone can no longer be relied on to end the leader: a cancelled `bajutsu
    run` has a SIGTERM handler installed and absorbs a second signal rather than dying on it, so that
    an operator who clicks Cancel twice does not lose the manifest — and a run whose main thread is
    wedged past executing Python never runs that handler at all, which leaves it answering neither
    signal. So the escalation ends on SIGKILL, the one signal nothing can absorb, and the grace window
    stays the whole of the delay this item adds to a cancel.

    The group-wide SIGTERM still comes first, and the group still gets a brief window to unwind on it:
    the leader never reached its own end-of-scenario teardown, so the backend driver children it would
    have stopped are still live, and a driver killed outright can leave a Simulator wedged for the
    next run.
    """
    if not _still_running(proc):
        return
    logger.warning(
        "a cancelled run did not finish within its %gs grace window; ending it",
        grace_seconds(),
    )
    _terminate(proc)
    with contextlib.suppress(subprocess.TimeoutExpired, OSError, ValueError):
        proc.wait(timeout=_ESCALATION_UNWIND_SECONDS)
    if not _still_running(proc):
        return
    logger.warning(
        "the cancelled run's process group ignored SIGTERM for %gs; sending SIGKILL",
        _ESCALATION_UNWIND_SECONDS,
    )
    _kill(proc)
    # `kill` only queues the signal, so the exit has to be waited for like the SIGTERM above it —
    # concluding anything from an immediate `poll()` would report a fault on every escalation that
    # worked. A process that really does outlive SIGKILL is stuck in the kernel (an uninterruptible
    # wait on a wedged device), which leaves `_run_job` blocked in `proc.wait()` and the job "running"
    # with no way for the Web UI to end it. Nothing here can fix that, so what was observed is stated
    # rather than the conclusion drawn: without this line the only trace is a job that never finishes.
    with contextlib.suppress(subprocess.TimeoutExpired, OSError, ValueError):
        proc.wait(timeout=_ESCALATION_UNWIND_SECONDS)
    if _still_running(proc):
        logger.warning(
            "the cancelled run has not exited %gs after SIGKILL; if its job never finishes, its "
            "process is stuck in the kernel and serve cannot end it",
            _ESCALATION_UNWIND_SECONDS,
        )


def _request_graceful_stop(proc: Any) -> None:
    """Ask a `run` job's process to close the run out itself, escalating past a grace window (BE-0370).

    Signals the leading process alone, not its group: for a `run` job the group also holds the backend
    driver the in-flight scenario is actuating through — a Playwright-launched browser, an
    `xcodebuild test` process — so a group-wide kill would tear that driver out from under the scenario
    and crash it before the next safe boundary, exactly the abrupt failure this path removes. The
    driver keeps running until the runner, noticing the request at its next boundary, tears it down
    through its own ordinary end-of-scenario teardown.

    The bound runs on its own timer rather than here, because `POST /api/jobs/{job_id}/cancel` calls
    `cancel_job` synchronously and the window has to exceed the longest single driver call (tens of
    seconds): holding the response open for it would leave the Cancel button unacknowledged long
    enough that an operator clicks it again. Past the deadline `_escalate` ends the run
    unconditionally, so a cancel is delayed by at most one grace period.
    """
    try:
        proc.send_signal(signal.SIGTERM)
    except (OSError, AttributeError, ValueError):
        # The leader could not be reached at all (already gone, or a proc double with no signal
        # channel), so there is nobody to answer the grace window: fall back to today's kill rather
        # than wait one out.
        _terminate(proc)
        return
    timer = threading.Timer(grace_seconds(), _escalate, args=(proc,))
    timer.daemon = True  # a serve shutdown must not block on a window nobody is waiting for
    timer.start()


def _register_proc(job: Job, proc: Any, *, graceful: bool = False) -> bool:
    """Attach *proc* as the job's live subprocess so a cancel request can reach it.  If a cancel
    already arrived, kill *proc* at once and return False so the caller stops before streaming.

    `graceful` records that *proc* answers a cancel cooperatively (BE-0370), so `cancel_job` can tell
    a `run` job's spawned run from its own build phase, which keeps today's kill.
    """
    with job.lock:
        if job.cancelled:
            kill = True
        else:
            job.proc = proc
            job.proc_graceful = graceful
            kill = False
    if kill:
        _terminate(proc)
    return not kill


def cancel_job(job: Job) -> bool:
    """Request cancellation of a running job: flag it and stop its current subprocess (the
    streamed output then ends and run_job marks the job done).  Returns False if already
    finished.

    A `run` job's spawned run is asked to stop cooperatively so it still writes its manifest, report,
    and history row (BE-0370); every other live subprocess — a `record` / `crawl` / triage spawn, and
    a `run` job's own build phase — is terminated outright, as it is today.
    """
    with job.lock:
        if job.status == "done":
            return False
        job.cancelled = True
        proc = job.proc
        graceful = job.proc_graceful
        noted = not job.lines or job.lines[-1] != "cancelled"
        if noted:
            job.lines.append("cancelled")
    if noted and job.bus is not None:
        job.bus.publish(job.id, "cancelled")
    if proc is not None:
        if graceful:
            _request_graceful_stop(proc)
        else:
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
        except (OSError, subprocess.CalledProcessError, _simctl.DeviceError) as e:
            # `DeviceError` covers the `DeviceTimeout` a wedged host now raises (BE-0363). It is
            # caught here rather than left to escape: this runs on a thread, so an uncaught
            # exception would leave `errors` empty and report the boot as having succeeded — the
            # cause-free failure BE-0363 exists to remove, moved to the `serve` job path.
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
            env=_spawn_env(job),
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
                job.proc_graceful = False
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
            # Exclude the log buffer — the lines already live in the bus's own stream (or, on the
            # server backend, in the worker's uploaded console.log), so duplicating them into the
            # done payload would needlessly bloat it.
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
    run_dir = (state.base_cwd / state.runs_dir / job.run_id).resolve()
    try:
        # serve holds none of the run's bound secret values — the run happened in a subprocess, or on
        # a remote worker — so the redactor is inert and only the sink's pattern backstop, which
        # needs no configuration, reaches this text (BE-0331). The sink also writes atomically, which
        # this rewrite already needed: the report viewer / list_runs may read the manifest
        # concurrently, and a plain write truncates first.
        reader = RunArtifactReader(run_dir)
        data = json.loads(reader.read_text("manifest.json"))
        # Merge, don't overwrite: the run subprocess already wrote a `provenance` block (scenario
        # fingerprint, and BE-0090's `uploadExec` decision). serve adds the upload identity it alone
        # knows; clobbering would drop both of the subprocess's records.
        existing = data.get("provenance")
        existing = existing if isinstance(existing, dict) else {}
        data["provenance"] = {**existing, **job.provenance}
        RunArtifactWriter(run_dir, Redactor(None)).write_json("manifest.json", data)
    except (OSError, ValueError):
        logger.warning("failed to record bundle provenance into %s", run_dir, exc_info=True)


def _persist_run(state: ServeState, job: Job) -> None:
    """Record a finished `run` into the system of record so the history list survives independently
    of the artifact store and is org-scoped (BE-0015), stamped with the run-history label the job
    was enqueued under (BE-0404 unit 2). A no-op only for a job that produced no run id
    (record/crawl, or a build/boot failure), and for a deployment with no repository (local /
    stdlib serve keeps no history table). With one, the run is recorded under its actor's org (the
    single `default` org for a token/CI run or an unknown user) so it shows in that org's history.

    Persistence must never break job finalization: this runs inside `run_job`'s `finally`, just
    before the live-log stream is closed, so any error (a missing org/user row, an FK violation on
    Postgres, a flaky DB) is caught and logged rather than stranding the stream."""
    if job.run_id is None:
        return
    run_id = job.run_id
    org = job.org
    if state.repository is None:
        return  # local / stdlib serve: no system of record to write into
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
                created_by=created_by,
                ok=ok,
                summary=_run_summary(run_id, manifest, ok=ok),
                scenario_hash=scenario_hash,
                tool_version=tool_version,
                git_revision=git_revision,
                device_runtime=_run_device_runtime(manifest),
                # The enqueue-time label wins: it is the only source for a cloud-batch run, whose
                # manifest comes back from the device cloud without one. The manifest is the fallback
                # for a job built outside the enqueue path.
                label=job.label or _run_str(manifest, "label"),
                target=_run_str(manifest, "target"),
            )
        )
    except Exception:
        logger.warning("failed to persist run %s to the system of record", run_id, exc_info=True)


def _run_str(manifest: dict[str, Any] | None, key: str) -> str | None:
    """A run's top-level string stamp (`target` / `label`) mirrored onto the DB record (BE-0404), so
    a per-target comparison and a label filter read the axis from the DB instead of re-reading every
    manifest. None for a run whose manifest is unreadable or predates the stamp — it drops out of
    the comparison rather than charting under a wrong target."""
    if manifest is None:
        return None
    value = manifest.get(key)
    return value if isinstance(value, str) and value else None


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


def _run_device_runtime(manifest: dict[str, Any] | None) -> str | None:
    """The OS label this run happened on, mirrored onto the DB record so flakiness groups per OS
    version straight from the DB (BE-0358) — `_run_summary` keeps no runtime, so this is the only
    channel. `""` records a run that named no single OS (no device catalog, or scenarios spanning
    versions); None is reserved for a manifest that couldn't be read at all, which leaves the row
    undetermined and therefore still eligible for the panel's backfill."""
    if manifest is None:
        return None
    run_os = device_os.from_manifest(manifest)
    return run_os.label if run_os is not None else ""


def _run_summary(run_id: str, manifest: dict[str, Any] | None, *, ok: bool) -> dict[str, Any]:
    """The run's history-list summary, from just this run's parsed `manifest.json` (not a full
    `list_runs()` scan, which re-reads every run's manifest from object storage). `write_report`
    writes `report.html` alongside the manifest, so a readable manifest means the report exists."""
    if manifest is None:
        return {
            "id": run_id,
            "ok": ok,
            "report": False,
            "scenarios": [],
            "passed": 0,
            "total": 0,
            "label": "",
            "target": "",
        }
    scenarios = [s for s in (manifest.get("scenarios") or []) if isinstance(s, dict)]
    return {
        "id": run_id,
        "ok": bool(manifest.get("ok")),
        "report": True,
        "scenarios": [str(s.get("scenario", "")) for s in scenarios],
        "passed": sum(1 for s in scenarios if s.get("ok")),
        "total": len(scenarios),
        # Mirrored so the DB-backed history list and the local artifact-store one carry the same
        # shape, and the label filter reads one field either way (BE-0404 unit 4).
        "label": str(manifest.get("label") or ""),
        "target": str(manifest.get("target") or ""),
    }


def _run_job(state: ServeState, job: Job) -> None:
    if job.batch is not None:
        # A cloud-batch job runs its scenario on a remote reserved device, not a local subprocess
        # (BE-0336). No device to boot, no app to build here — the provider packages and submits.
        _run_batch_job(state, job)
        return
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
        env=_spawn_env(job),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,  # own process group, so a cancel stops its children (e.g. `claude -p`)
    )
    if not _register_proc(job, proc, graceful=job.graceful_cancel):
        proc.wait()
        with job.lock:
            job.exit_code, job.status, job.proc = proc.returncode or 1, "done", None
            job.proc_graceful = False
        return
    # Read while the leader is alive, for the post-exit sweep below (see `_pgid_of`).
    pgid = _pgid_of(proc)
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
        swept = job.cancelled and job.proc_graceful
        job.proc = None
        job.proc_graceful = False
        job.exit_code = proc.returncode
        job.status = "done"
        # A record that paused for a human but ended without a response (a StreamHandoff timeout →
        # cancel, or a killed job) must not report awaiting-human on its terminal view — the process
        # is gone and cannot be resumed (BE-0179).
        job.awaiting_human = False
    if swept:
        _sweep_group(pgid)


@dataclass
class _RepositoryBatchCheckpoint:
    """Persist and resume a cloud-batch run's scheduled ARN through the system of record (Unit 5).

    Keyed by job id on the ``jobs`` row, so a worker that re-leases the job after a restart resumes the
    scheduled run instead of resubmitting it.
    """

    repo: Repository
    job_id: str

    def load(self) -> str | None:
        return self.repo.load_batch_run_arn(self.job_id)

    def save(self, run_arn: str) -> None:
        self.repo.save_batch_run_arn(self.job_id, run_arn)


def _batch_checkpoint(state: ServeState, job_id: str) -> _RepositoryBatchCheckpoint | None:
    """A durable checkpoint when the hosted DB backend is wired, else None (BE-0336 Unit 5).

    The local single-process backend keeps only the in-process best-effort path — a restart there loses
    all in-memory state regardless, so there is nothing durable to resume from."""
    repo = state.repository
    if repo is None:
        return None
    return _RepositoryBatchCheckpoint(repo, job_id)


def _run_batch_job(state: ServeState, job: Job) -> None:
    """Run a cloud-batch job (BE-0336): submit its one scenario to the named batch provider, land the
    downloaded run under ``runs_dir`` so serve records and renders it like a local run, and set the
    job's verdict from the run's own manifest — no local device, no subprocess, off the verdict path.

    Note: cloud-batch dispatch currently works only through the in-process serve path. The DB-queue
    worker path (``bajutsu/serve/server/worker_job.py``) builds its own ``ServeState`` without
    ``devicefarm_package_root``, so it falls back to the ephemeral workspace — a directory that holds
    only the scenario and config, not Bajutsu's ``tests/`` or ``pyproject.toml``. Any cloud-batch job
    leased from the DB queue will fail Device Farm's ``APPIUM_PYTHON_TEST_PACKAGE`` validation until
    the worker's ``ServeState`` is wired with the package root and a host-portable app artifact path.
    """
    request = job.batch
    if (
        request is None
    ):  # _run_job routes here only when batch is set; the guard keeps intent + mypy honest
        return
    # Lazy import: only a cloud-batch job needs the batch seam (and its provider's optional deps), so
    # the default serve path never pulls it in.
    from bajutsu.serve import batch_provider

    try:
        provider = batch_provider.resolve(request.provider)
    except ValueError as exc:
        _fail_batch(job, str(exc))
        return
    runs_root = (state.base_cwd / state.runs_dir).resolve()
    with tempfile.TemporaryDirectory() as download_name:
        try:
            verdict = provider.submit(
                request,
                # Same package root start_run_set relativized the config/scenario paths against (see
                # ServeState.devicefarm_package_root), falling back to the job's own cwd then state.cwd.
                work_dir=state.devicefarm_package_root or job.cwd or state.cwd,
                dest=Path(download_name),
                checkpoint=_batch_checkpoint(state, job.id),
            )
        except Exception as exc:
            # A provider or submission failure is a failed run, never a stranded worker thread: catch
            # broadly (like `_persist_run`'s finalization guards) and surface it as a loud FAIL. Log the
            # traceback for operational debugging before failing the job (the surfaced line carries only
            # the message), consistent with the other finally-guarded paths in this module.
            logger.warning("cloud-batch job %s submission failed", job.id, exc_info=True)
            _fail_batch(job, f"cloud-batch run failed: {exc}")
            return
        try:
            run_id = _land_batch_run(Path(download_name), runs_root)
        except OSError as exc:
            # Landing runs outside the submit guard above, but its `mkdir`/`move` can still raise
            # (permission, disk full, cross-device rename). `run_job` has no `except`, so an escape here
            # would strand the worker with the job stuck "running" — fail loud instead.
            logger.warning("cloud-batch job %s failed to land its run", job.id, exc_info=True)
            _fail_batch(job, f"cloud-batch run failed to land: {exc}")
            return
    lines = [
        f"bajutsu verdict: {'PASS' if verdict.ok else 'FAIL'} ({verdict.passed}/{verdict.total})"
    ]
    if verdict.failures:
        lines.append("failed scenarios: " + ", ".join(verdict.failures))
    with job.lock:
        if run_id is not None:
            job.run_id = run_id
        job.lines.extend(lines)
        job.exit_code = 0 if verdict.ok else 1
        job.status = "done"
    if job.bus is not None:
        # Publish every line (not just the verdict), so a bus-streaming client also learns which
        # scenarios failed — otherwise it sees "FAIL" but never the `failed scenarios:` detail.
        for line in lines:
            job.bus.publish(job.id, line)


def _fail_batch(job: Job, message: str) -> None:
    """Fail a cloud-batch job loudly: surface `message`, set a non-zero exit, and mark it done — a
    provider or submission error is a failed run, never a silent hang (determinism first)."""
    with job.lock:
        job.lines.append(message)
        job.exit_code = 1
        job.status = "done"
    if job.bus is not None:
        job.bus.publish(job.id, message)
    logger.warning("cloud-batch job %s failed: %s", job.id, message)


def _land_batch_run(download_dir: Path, runs_root: Path) -> str | None:
    """Move the downloaded run directory (the one holding ``manifest.json``) under ``runs_root`` as
    ``<run_id>/``, so serve's local artifact store, provenance stamp, and report viewer find it just as
    they do a local run. Returns the run id only when the run was actually landed, or None when the
    download carried no manifest (a failed cloud run the verdict already reports as a fail), the run id
    is unsafe, or the id already exists under ``runs_root``."""
    # A run lands as `runs/<run_id>/manifest.json`, but Device Farm's Customer Artifacts zip nests it
    # under a `Host_Machine_Files/$DEVICEFARM_LOG_DIR/` prefix rather than at the download root. Glob
    # the whole tree (as `verdict_from_manifest` does), but keep only manifests under a `runs/` dir so
    # a stray manifest elsewhere can't move an arbitrary directory — `valid_run_id` below is the actual
    # guard that the moved directory can't escape runs_root.
    manifests = sorted(
        m for m in download_dir.rglob("manifest.json") if m.parent.parent.name == "runs"
    )
    if not manifests:
        return None
    if len(manifests) > 1:
        # `verdict_from_manifest` aggregates the verdict across *every* manifest, but we land only this
        # one. The two disagree the moment a download carries more than one manifest, so warn loudly
        # rather than drop the extra runs silently (determinism first / fail loud).
        logger.warning(
            "cloud-batch download carried more than one manifest (%d); landing only %r and reporting a "
            "verdict aggregated across all of them",
            len(manifests),
            manifests[0].parent.name,
        )
    run_dir = manifests[0].parent
    # Note: an explicit symlink guard is unnecessary. `Path.rglob` uses `recurse_symlinks=False` by
    # default (Python 3.13), so a symlinked run directory is never descended into and its
    # `manifest.json` never appears in `manifests` — `_land_batch_run` returns None via the
    # `if not manifests` branch above before `run_dir` is ever computed.
    run_id = run_dir.name
    if not valid_run_id(run_id):
        # A remote run id that isn't a single safe path segment must not escape runs_root: leave the
        # run unlanded rather than write astray. The verdict still stands on the job.
        logger.warning("cloud-batch run id %r is not a safe segment; not landing it", run_id)
        return None
    target = runs_root / run_id
    if target.exists():
        # A fresh cloud run id shouldn't collide; if it does, an existing dir under this id belongs to
        # another run, so claiming it would point persistence/rendering at unrelated data. Don't land it
        # (and don't overwrite the existing run) — return None so the run is left unrecorded, loudly.
        logger.warning(
            "cloud-batch run id %r already exists under runs_dir; not landing it", run_id
        )
        return None
    runs_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(target))
    return run_id

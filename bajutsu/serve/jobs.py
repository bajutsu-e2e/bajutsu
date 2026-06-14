"""Job lifecycle: state, spawning, cancellation, device boot, and app build."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bajutsu import env

# The run command prints "PASS/FAIL  runs/<id>/manifest.json"; pull <id> from it.
_RUN_ID_RE = re.compile(r"runs/([0-9A-Za-z._-]+)/manifest\.json")

Popen = Callable[..., Any]


@dataclass
class Job:
    id: str
    cmd: list[str]
    udids: list[str] = field(default_factory=list)  # devices to boot before the run
    app_path: str | None = None  # built .app the run needs; built on demand if missing
    build: str | None = None  # shell command that builds app_path (None = no on-demand build)
    status: str = "running"  # running | done
    exit_code: int | None = None
    run_id: str | None = None  # the runs/<id> a `run` job produced, parsed from its output
    out_path: str | None = None  # the scenario a `record` job authored (so the UI can load it)
    cancelled: bool = False  # a /cancel request stopped this job (vs. a real pass/fail)
    proc: Any = None  # the live subprocess (build or run), so a cancel can terminate it
    lines: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def view(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "exitCode": self.exit_code,
                "runId": self.run_id,
                "outPath": self.out_path,
                "cancelled": self.cancelled,
                "ok": (self.exit_code == 0 and not self.cancelled)
                if self.status == "done"
                else None,
                "lines": list(self.lines),
            }


@dataclass
class ServeState:
    scenarios_dir: Path
    config: Path
    runs_dir: Path
    cwd: Path = field(default_factory=Path.cwd)
    popen: Popen = subprocess.Popen
    simctl: env.RunFn = env._real_run  # runs `xcrun simctl …` (booting devices, listing them)
    jobs: dict[str, Job] = field(default_factory=dict)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def new_job(
        self,
        cmd: list[str],
        udids: list[str] | None = None,
        app_path: str | None = None,
        build: str | None = None,
        out_path: str | None = None,
    ) -> Job:
        with self._lock:
            self._seq += 1
            job = Job(
                id=str(self._seq),
                cmd=cmd,
                udids=list(udids or []),
                app_path=app_path,
                build=build,
                out_path=out_path,
            )
            self.jobs[job.id] = job
        return job


def _spawn_env() -> dict[str, str]:
    """Ensure the venv bin dir (where the ``idb`` client lives) is on PATH for the run."""
    e = dict(os.environ)
    bindir = str(Path(sys.executable).parent)
    e["PATH"] = bindir + os.pathsep + e.get("PATH", "")
    return e


def _log(job: Job, line: str) -> None:
    with job.lock:
        job.lines.append(line)


def _terminate(proc: Any) -> None:
    """Best-effort stop of a live subprocess; ignore an already-exited / fake proc."""
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
        if not job.lines or job.lines[-1] != "cancelled":
            job.lines.append("cancelled")
    if proc is not None:
        _terminate(proc)
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
            state.simctl(env.bootstatus_cmd(udid), None)
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
    if (state.cwd / job.app_path).exists():
        return True
    _log(job, f"app binary missing ({job.app_path}) — building: {job.build}")
    try:
        proc = state.popen(
            shlex.split(job.build),
            cwd=str(state.cwd),
            env=_spawn_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
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
    ``job.cmd``, capturing combined output line-by-line and the produced run id."""
    if not _boot_devices(state, job):
        return
    if not _build_app(state, job):
        return
    proc = state.popen(
        job.cmd,
        cwd=str(state.cwd),
        env=_spawn_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if not _register_proc(job, proc):
        proc.wait()
        with job.lock:
            job.exit_code, job.status, job.proc = proc.returncode or 1, "done", None
        return
    try:
        for raw in proc.stdout or []:
            line = raw.rstrip("\n")
            match = _RUN_ID_RE.search(line)
            with job.lock:
                job.lines.append(line)
                if match:
                    job.run_id = match.group(1)
    except OSError:
        _terminate(proc)
    proc.wait()
    with job.lock:
        job.proc = None
        job.exit_code = proc.returncode
        job.status = "done"

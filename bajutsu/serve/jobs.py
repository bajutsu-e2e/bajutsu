"""Job lifecycle: state, spawning, cancellation, device boot, and app build."""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bajutsu.serve.server.db import Repository
    from bajutsu.serve.server.oauth import OAuthClient

from bajutsu import env
from bajutsu.serve.artifacts import ArtifactStore, LocalArtifactStore
from bajutsu.serve.baselines import BaselineStore, LocalBaselineStore
from bajutsu.serve.executor import LocalExecutor, RunExecutor
from bajutsu.serve.helpers import app_scenarios_dir
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.scenarios import LocalScenarioStore, ScenarioStore
from bajutsu.serve.sessions import InMemorySessionStore, SessionStore

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
    actor: str | None = None  # the GitHub login that started it, for per-user quota (BE-0015 7c-3)
    proc: Any = None  # the live subprocess (build or run), so a cancel can terminate it
    lines: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    bus: LogBus | None = None  # live-log channel; set from state.logbus at creation (BE-0015)
    # Files a remote worker must write into its workspace before running (workspace-relative path ->
    # content): the scenario + config a server-backend run materializes. Empty for local (the files
    # are already on disk). Travels in the job spec; never carries a client-controlled path (BE-0015).
    materials: dict[str, str] = field(default_factory=dict)
    # For a server-backend `record`: (app, ref) the worker persists the authored scenario to after
    # the run (it wrote it to `out_path` in its workspace). None for local / non-record jobs.
    record_save: tuple[str, str] | None = None
    # For a server-backend `run`: download the visual baselines into the workspace before running
    # (the cmd points `--baselines` at a workspace dir). False for local (the real dir is used).
    materialize_baselines: bool = False

    def view(self, *, include_lines: bool = True) -> dict[str, Any]:
        """The job's state for the UI. `include_lines=False` omits the log buffer — used for the
        terminal-status payload stored on the LogBus, where the lines already live in the log
        stream and would needlessly duplicate the whole log (BE-0015 W2)."""
        with self.lock:
            v: dict[str, Any] = {
                "id": self.id,
                "status": self.status,
                "exitCode": self.exit_code,
                "runId": self.run_id,
                "outPath": self.out_path,
                "cancelled": self.cancelled,
                "ok": (self.exit_code == 0 and not self.cancelled)
                if self.status == "done"
                else None,
            }
            if include_lines:
                v["lines"] = list(self.lines)
            return v


@dataclass
class ServeState:
    runs_dir: Path
    config: Path | None = None  # None until a config is opened from the UI
    scenarios_dir: Path | None = None  # override; default is the selected app's configured dir
    root: Path = field(default_factory=Path.cwd)  # the file browser's browse ceiling
    # where `visual` baselines live (and where Approve promotes to); serve() defaults it to
    # <scenarios_dir>/baselines.
    baselines_dir: Path = field(default_factory=lambda: Path("baselines"))
    cwd: Path = field(default_factory=Path.cwd)
    popen: Popen = subprocess.Popen
    # How a created job gets executed. Defaults to in-process threads (LocalExecutor); a server
    # backend swaps in a queue-based executor without touching the handler or run_job (BE-0015).
    executor: RunExecutor = field(default_factory=LocalExecutor)
    # Live-log delivery. In-memory buffer by default; a server backend swaps in a Redis stream
    # so any replica can serve any job's `/events` (BE-0015).
    logbus: LogBus = field(default_factory=InMemoryLogBus)
    # Run-artifact reads. Filesystem-confined by default; a server backend swaps in an
    # object-storage store (set after construction) that may serve signed-URL redirects (BE-0015).
    artifacts: ArtifactStore = field(init=False)
    # Scenario resolution. Confined to the app's scenarios dir by default; a server backend swaps
    # in a per-project store (set after construction) that resolves by id (BE-0015).
    scenarios: ScenarioStore = field(init=False)
    # Visual-regression baselines. Filesystem-confined by default; a server backend swaps in an
    # object-storage store (set after construction) (BE-0015).
    baselines: BaselineStore = field(init=False)
    # The system of record (BE-0015 7a). None until a database is wired: local never has one, and a
    # server backend assigns a SqlRepository only when BAJUTSU_DATABASE_URL is set, so behavior is
    # unchanged without one. Annotated as a string (lazy) so the default path never loads SQLAlchemy.
    repository: Repository | None = None
    simctl: env.RunFn = env._real_run  # runs `xcrun simctl …` (booting devices, listing them)
    jobs: dict[str, Job] = field(default_factory=dict)
    # Cap on concurrently-running run/record jobs so one caller can't monopolize the scarce device
    # (BE-0051). <= 0 means unlimited; serve() sets it from --max-concurrent-runs (default 4).
    max_concurrent: int = 4
    # Per-user cap on concurrent jobs (BE-0015 7c-3), so one OAuth user can't starve the pool. <= 0
    # means unlimited (the default); a server backend sets it from BAJUTSU_MAX_CONCURRENT_PER_USER.
    # Applies only to jobs that carry an actor (an OAuth identity); token/anonymous jobs are exempt.
    max_concurrent_per_user: int = 0
    # Optional shared token (BE-0051). None = open (loopback-only legacy behavior); when set, every
    # request must authenticate. Login exchanges it for an opaque session id held by the `sessions`
    # seam below — the shared token itself never lives in the browser.
    token: str | None = None
    # Login sessions (BE-0051). Default in-memory (a restart drops them); a server backend swaps in
    # a Redis-backed store so sessions survive restarts and span control-plane processes (BE-0015
    # 7b). The shared token itself never lives in the browser — only an opaque session id does.
    sessions: SessionStore = field(default_factory=InMemorySessionStore)
    # GitHub OAuth login (BE-0015 7b-2). None = OAuth not configured (shared-token auth only); a
    # server backend with the OAuth env set injects a client. `oauth_allowed_users` is the GitHub
    # login allowlist — only these may log in. Both string-annotated/lazy so the default path is
    # OAuth-free. Identity from a successful login is bound to the session.
    oauth: OAuthClient | None = None
    oauth_allowed_users: frozenset[str] = frozenset()
    # RBAC role policy (BE-0015 7c-2): logins to grant admin / viewer; everyone else allowed is an
    # editor. Recomputed into each user's stored role on login.
    oauth_admins: frozenset[str] = frozenset()
    oauth_viewers: frozenset[str] = frozenset()
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # `artifacts`/`scenarios` are init=False so existing ServeState(...) calls don't change;
        # default them to the local stores here (a server backend overwrites them afterwards).
        self.artifacts = LocalArtifactStore(self.runs_dir)
        # Resolve the dir lazily through a closure so a config opened from the UI later is reflected.
        self.scenarios = LocalScenarioStore(lambda app: _scenarios_dir_for(self, app))
        self.baselines = LocalBaselineStore(self.baselines_dir)

    def check_token(self, candidate: str) -> bool:
        """Constant-time compare of a presented token against the configured one."""
        return self.token is not None and secrets.compare_digest(candidate, self.token)

    def issue_session(self, identity: str | None = None) -> str:
        """Mint and remember a new opaque session id (returned to set as a cookie at login),
        optionally bound to *identity* (the GitHub login from an OAuth login)."""
        return self.sessions.issue(identity)

    def valid_session(self, sid: str) -> bool:
        return self.sessions.valid(sid)

    def active_jobs(self) -> int:
        """How many spawned jobs are still running (not yet finished)."""
        with self._lock:
            return sum(1 for j in self.jobs.values() if j.status == "running")

    def _make_job(
        self,
        cmd: list[str],
        udids: list[str] | None,
        app_path: str | None,
        build: str | None,
        out_path: str | None,
        materials: dict[str, str] | None,
        record_save: tuple[str, str] | None,
        materialize_baselines: bool,
        actor: str | None,
    ) -> Job:
        """Create + register a job. Caller must hold ``self._lock``."""
        self._seq += 1
        job = Job(
            id=str(self._seq),
            cmd=cmd,
            udids=list(udids or []),
            app_path=app_path,
            build=build,
            out_path=out_path,
            bus=self.logbus,
            materials=dict(materials or {}),
            record_save=record_save,
            materialize_baselines=materialize_baselines,
            actor=actor,
        )
        self.jobs[job.id] = job
        return job

    def new_job(
        self,
        cmd: list[str],
        udids: list[str] | None = None,
        app_path: str | None = None,
        build: str | None = None,
        out_path: str | None = None,
        materials: dict[str, str] | None = None,
        record_save: tuple[str, str] | None = None,
        materialize_baselines: bool = False,
        actor: str | None = None,
    ) -> Job:
        with self._lock:
            return self._make_job(
                cmd,
                udids,
                app_path,
                build,
                out_path,
                materials,
                record_save,
                materialize_baselines,
                actor,
            )

    def try_new_job(
        self,
        cmd: list[str],
        udids: list[str] | None = None,
        app_path: str | None = None,
        build: str | None = None,
        out_path: str | None = None,
        materials: dict[str, str] | None = None,
        record_save: tuple[str, str] | None = None,
        materialize_baselines: bool = False,
        actor: str | None = None,
    ) -> Job | None:
        """Create a job only if under the concurrency caps, counting and inserting atomically under
        the lock so two concurrent dispatches can't both slip past a cap (BE-0051). Returns None at
        the global cap, or — for an identified *actor* — at the per-user cap (BE-0015 7c-3)."""
        with self._lock:
            running = [j for j in self.jobs.values() if j.status == "running"]
            if self.max_concurrent > 0 and len(running) >= self.max_concurrent:
                return None
            if actor and self.max_concurrent_per_user > 0:
                mine = sum(1 for j in running if j.actor == actor)
                if mine >= self.max_concurrent_per_user:
                    return None
            return self._make_job(
                cmd,
                udids,
                app_path,
                build,
                out_path,
                materials,
                record_save,
                materialize_baselines,
                actor,
            )


def _scenarios_dir_for(state: ServeState, app: str | None) -> Path | None:
    """The scenarios dir to list/save for *app*: the ``--scenarios`` override if set, else the
    app's configured dir.  None when neither is available."""
    if state.scenarios_dir is not None:
        return state.scenarios_dir
    if state.config is None or not app:
        return None
    return app_scenarios_dir(state.config, app)


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
        noted = not job.lines or job.lines[-1] != "cancelled"
        if noted:
            job.lines.append("cancelled")
    if noted and job.bus is not None:
        job.bus.publish(job.id, "cancelled")
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
    ``job.cmd``, capturing combined output line-by-line and the produced run id. The job's live
    log channel is closed on every exit path, so an ``/events`` subscriber's stream always ends."""
    try:
        _run_job(state, job)
    finally:
        if job.bus is not None:  # run_job returning means the job finished — end the live stream
            # Record the terminal status on the bus so a control-plane replica reading a
            # worker-run job sees the real exit/run id (its own Job stays "running") (BE-0015 W2).
            # Exclude the log buffer — the lines already live in the bus's stream, so duplicating
            # them into the done payload would needlessly bloat it (Redis memory).
            job.bus.close(job.id, json.dumps(job.view(include_lines=False)))


def _run_job(state: ServeState, job: Job) -> None:
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
            if job.bus is not None:
                job.bus.publish(job.id, line)
    except OSError:
        _terminate(proc)
    proc.wait()
    with job.lock:
        job.proc = None
        job.exit_code = proc.returncode
        job.status = "done"

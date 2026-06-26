"""Job lifecycle: state, spawning, cancellation, device boot, and app build."""

from __future__ import annotations

import contextlib
import json
import logging
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
from bajutsu.config import DEFAULT_ORG
from bajutsu.serve.artifacts import ArtifactStore, LocalArtifactStore
from bajutsu.serve.baselines import BaselineStore, LocalBaselineStore
from bajutsu.serve.executor import LocalExecutor, RunExecutor
from bajutsu.serve.helpers import target_scenarios_dir
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.scenarios import LocalScenarioStore, ScenarioStore
from bajutsu.serve.sessions import InMemorySessionStore, SessionStore

logger = logging.getLogger(__name__)

# The run command prints "PASS/FAIL  runs/<id>/manifest.json"; pull <id> from it.
_RUN_ID_RE = re.compile(r"runs/([0-9A-Za-z._-]+)/manifest\.json")

# The org an unassigned user/app falls into. Re-exported from config (the org model's home) so job
# persistence and the operations layer share one source of truth.
_DEFAULT_ORG = DEFAULT_ORG

Popen = Callable[..., Any]


@dataclass
class Job:
    # `id` is assigned by `ServeState.register`/`try_register` (from the job sequence); a caller
    # builds a Job without one. The worker rebuilds a Job with the control-plane id passed in.
    id: str = ""
    cmd: list[str] = field(default_factory=list)
    udids: list[str] = field(default_factory=list)  # devices to boot before the run
    app_path: str | None = None  # built .app the run needs; built on demand if missing
    build: str | None = None  # shell command that builds app_path (None = no on-demand build)
    status: str = "running"  # running | done
    exit_code: int | None = None
    run_id: str | None = None  # the runs/<id> a `run` job produced, parsed from its output
    out_path: str | None = None  # the scenario a `record` job authored (so the UI can load it)
    cancelled: bool = False  # a /cancel request stopped this job (vs. a real pass/fail)
    actor: str | None = None  # the GitHub login that started it, for per-user quota (BE-0015 7c-3)
    # The org the run belongs to (BE-0015 multi-tenancy). Travels in the job spec so a remote worker
    # reads/writes this org's object-store prefix. The single `default` org for local / single-tenant.
    org: str = _DEFAULT_ORG
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
class StoreBundle:
    """The three per-tenant storage seams resolved for one org (BE-0015 multi-tenancy). Operations
    fetch a bundle for the request's org and use it instead of the bare `ServeState` fields, so a
    server backend keeps each org's artifacts/scenarios/baselines under its own object-store prefix.
    Local serve has one tenant, so its bundle is just the default stores."""

    artifacts: ArtifactStore
    scenarios: ScenarioStore
    baselines: BaselineStore


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
    # Per-org store factory (BE-0015 multi-tenancy). None on local serve (one tenant); a server
    # backend sets a closure that builds object stores prefixed for the given org. `for_org` falls
    # back to the default stores when unset, so local behavior is unchanged.
    org_stores: Callable[[str], StoreBundle] | None = None
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # `artifacts`/`scenarios` are init=False so existing ServeState(...) calls don't change;
        # default them to the local stores here (a server backend overwrites them afterwards).
        self.artifacts = LocalArtifactStore(self.runs_dir)
        # Resolve the dir lazily through a closure so a config opened from the UI later is reflected.
        self.scenarios = LocalScenarioStore(lambda target: _scenarios_dir_for(self, target))
        self.baselines = LocalBaselineStore(self.baselines_dir)

    def org_of(self, actor: str | None) -> str:
        """The org of *actor*, read from their persisted user row (assigned at login). The single
        `default` org without a database, without an identity, or for an unknown user (BE-0015)."""
        if self.repository is None or not actor:
            return _DEFAULT_ORG
        return self.repository.user_org(actor) or _DEFAULT_ORG

    def for_org(self, org: str) -> StoreBundle:
        """The storage seams scoped to *org*. A server backend prefixes each org's objects; local
        serve has a single tenant, so this is just the default stores (BE-0015 multi-tenancy)."""
        if self.org_stores is not None:
            return self.org_stores(org)
        return StoreBundle(self.artifacts, self.scenarios, self.baselines)

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

    def _register(self, job: Job) -> Job:
        """Assign the job its id + live-log bus and store it. Caller must hold ``self._lock``. The
        caller builds a fresh `Job` (the dataclass is the single source of truth for its fields), so
        adding a field never touches this layer. Single-use: registering a job that already has an id
        is a programming error (it would orphan the earlier `state.jobs` entry)."""
        if job.id:
            raise ValueError(f"job {job.id!r} is already registered")
        self._seq += 1
        job.id = str(self._seq)
        job.bus = self.logbus
        # Don't alias caller-owned collections (preserves the prior new_job semantics): a later edit
        # to the list/dict the caller passed must not mutate the registered job.
        job.udids = list(job.udids)
        job.materials = dict(job.materials)
        self.jobs[job.id] = job
        return job

    def register(self, job: Job) -> Job:
        with self._lock:
            return self._register(job)

    def try_register(self, job: Job) -> Job | None:
        """Register *job* only if under the concurrency caps, counting and inserting atomically under
        the lock so two concurrent dispatches can't both slip past a cap (BE-0051). Returns None at
        the global cap, or — for an identified ``job.actor`` — at the per-user cap (BE-0015 7c-3)."""
        with self._lock:
            running = [j for j in self.jobs.values() if j.status == "running"]
            if self.max_concurrent > 0 and len(running) >= self.max_concurrent:
                return None
            if job.actor and self.max_concurrent_per_user > 0:
                mine = sum(1 for j in running if j.actor == job.actor)
                if mine >= self.max_concurrent_per_user:
                    return None
            return self._register(job)


def _scenarios_dir_for(state: ServeState, target: str | None) -> Path | None:
    """The scenarios dir to list/save for *target*: the ``--scenarios`` override if set, else the
    target's configured dir.  None when neither is available.

    A configured dir is **relative to the config's base** — `state.cwd` — so a Git-sourced config
    (whose `cwd` is the checkout root) lists scenarios from the fetched tree, not serve's launch
    directory. For a local config `state.cwd` is serve's launch dir, so this is unchanged (BE-0063)."""
    if state.scenarios_dir is not None:
        return state.scenarios_dir
    if state.config is None or not target:
        return None
    configured = target_scenarios_dir(state.config, target)
    if configured is None or configured.is_absolute():
        return configured
    return state.cwd / configured


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
        _persist_run(state, job)
        if job.bus is not None:  # run_job returning means the job finished — end the live stream
            # Record the terminal status on the bus so a control-plane replica reading a
            # worker-run job sees the real exit/run id (its own Job stays "running") (BE-0015 W2).
            # Exclude the log buffer — the lines already live in the bus's stream, so duplicating
            # them into the done payload would needlessly bloat it (Redis memory).
            job.bus.close(job.id, json.dumps(job.view(include_lines=False)))


def _persist_run(state: ServeState, job: Job) -> None:
    """Record a finished `run` into the system of record so the history list survives independently
    of the artifact store and is org-scoped (BE-0015). A no-op without a repository (local / stdlib
    serve) or for a job that produced no run id (record/crawl, or a build/boot failure). The run is
    recorded under its actor's org (the single `default` org for a token/CI run or an unknown user),
    so it shows in that org's history.

    Persistence must never break job finalization: this runs inside `run_job`'s `finally`, just
    before the live-log stream is closed, so any error (a missing org/user row, an FK violation on
    Postgres, a flaky DB) is caught and logged rather than stranding the stream."""
    if state.repository is None or job.run_id is None:
        return
    repo, run_id = state.repository, job.run_id
    try:
        # Lazy import: only a server backend has a repository, where SQLAlchemy is already loaded,
        # so the default serve path never pulls server.db in (the import guard stays green).
        from bajutsu.serve.server.db import RunRecord

        ok = job.exit_code == 0 and not job.cancelled
        # The run's org was decided at job creation (and travels to a worker in the spec). Attribute
        # `created_by` only to a user that actually exists, so the foreign key can't fail (a token /
        # CI run has no actor; an OAuth run's user was upserted at login).
        org = job.org
        repo.ensure_org(org, slug=org, name=org)
        created_by = job.actor if job.actor and repo.user_org(job.actor) is not None else None
        repo.record_run(
            RunRecord(
                id=run_id,
                org_id=org,
                status="done",
                created_by=created_by,
                ok=ok,
                summary=_run_summary(state, run_id, ok=ok),
            )
        )
    except Exception:
        logger.warning("failed to persist run %s to the system of record", run_id, exc_info=True)


def _run_summary(state: ServeState, run_id: str, *, ok: bool) -> dict[str, Any]:
    """The run's history-list summary, read from just this run's `manifest.json` (not a full
    `list_runs()` scan, which re-reads every run's manifest from object storage). `write_report`
    writes `report.html` alongside the manifest, so a readable manifest means the report exists."""
    minimal = {"id": run_id, "ok": ok, "report": False, "scenarios": [], "passed": 0, "total": 0}
    raw = state.artifacts.open_bytes(f"{run_id}/manifest.json")
    if raw is None:
        return minimal
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return minimal
    scenarios = [s for s in (data.get("scenarios") or []) if isinstance(s, dict)]
    return {
        "id": run_id,
        "ok": bool(data.get("ok")),
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

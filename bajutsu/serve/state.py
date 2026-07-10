"""Serve state container: the `ServeState` shared by the serve package and the value types it holds.

Split from `serve/jobs.py` (BE-0206): most of the serve package reads `ServeState` (and the `Job`,
`StoreBundle`, `CaptureSession` value types), while only the run/cancel execution engine — which
stays in `jobs.py` — mutates a `Job`. The runtime dependency is one-directional: `state` imports
`executor` at runtime (for the `LocalExecutor` field default), while `executor` references
`ServeState`/`Job` only under `TYPE_CHECKING` and imports `run_job` lazily — avoiding a
`state ⇄ executor` cycle. The state module keeps the file from growing on two axes at once.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bajutsu.serve.provider_store import ProviderSettingsStore
    from bajutsu.serve.server.db import Repository
    from bajutsu.serve.server.oauth import OAuthClient

from bajutsu import simctl as _simctl
from bajutsu.drivers import base as driver_base
from bajutsu.object_store import EvidenceTarget, ObjectStore
from bajutsu.redaction import Redactor
from bajutsu.scenario.models import Step
from bajutsu.serve.artifacts import ArtifactStore, LocalArtifactStore
from bajutsu.serve.baselines import BaselineStore, LocalBaselineStore
from bajutsu.serve.executor import LocalExecutor, RunExecutor
from bajutsu.serve.helpers import target_scenarios_dir
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.scenarios import LocalScenarioStore, ScenarioStore
from bajutsu.serve.secrets import EnvSecretStore, SecretStore
from bajutsu.serve.sessions import InMemorySessionStore, SessionStore
from bajutsu.serve.uploads import Upload

# The org an unassigned user/app falls into. Re-exported from serve.orgs (the org model's home) so
# job persistence and the operations layer share one source of truth.
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
    # Working directory override for the spawned run/build (default: state.cwd, which already points
    # at a Git checkout or an uploaded bundle when one is bound). None uses state.cwd.
    cwd: Path | None = None
    # Provenance to record into the produced run's manifest.json after it finishes (the bound bundle's
    # filename + zip sha256 + size). None for a normal run. Set for a run off an uploaded bundle (BE-0073).
    provenance: dict[str, str] | None = None
    # Per-run key prefix for evidence upload, under the server's --evidence-store base (BE-0110). CI
    # sets it via the /api/run body to pick the cloud lifecycle policy; travels in the job spec so the
    # worker relays it back when requesting presigned PUT URLs. Empty = key directly under the base.
    evidence_prefix: str = ""
    # A `record` job that paused for a human is in an explicit, resumable "awaiting human" state
    # (BE-0179): set when the spawned record emits a handoff request, cleared when the response is
    # written back to its stdin. Surfaced to the UI so the paused job is visible, not a silent block.
    awaiting_human: bool = False

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
                "awaitingHuman": self.awaiting_human,
            }
            if include_lines:
                v["lines"] = list(self.lines)
            return v


@dataclass
class StoreBundle:
    """The four per-tenant storage seams resolved for one org (BE-0015 multi-tenancy). Operations
    fetch a bundle for the request's org and use it instead of the bare `ServeState` fields, so a
    server backend keeps each org's artifacts/scenarios/baselines/secrets under its own
    object-store prefix. Local serve has one tenant, so its bundle is just the default stores."""

    artifacts: ArtifactStore
    scenarios: ScenarioStore
    baselines: BaselineStore
    secrets: SecretStore


@dataclass
class ProviderSettings:
    """One AI provider's remembered model/effort/region for the serve session (BE-0183).

    Scopes the fields to the provider they belong to, so switching the Settings dropdown no longer
    overwrites what was set for the provider left behind. `region` applies to `bedrock` only; the
    SDK/CLI providers leave it empty. Held in memory and materialized into env vars; on local serve
    it is also persisted through `provider_settings_store` so a saved choice survives a restart
    (BE-0184).
    """

    model: str = ""
    effort: str = ""
    region: str = ""


@dataclass
class CaptureSession:
    """Live state for an active capture session (BE-0012).

    Holds the in-process Driver across mark requests — the one architectural departure from
    the stateless shell-out pattern. A single-session guard prevents two concurrent captures
    on the same state.
    """

    driver: driver_base.Driver
    target: str
    elements: list[driver_base.Element]
    screen_size: tuple[float, float]
    namespaces: list[str]
    redactor: Redactor | None
    actor: str | None = None
    steps: list[Step] = field(default_factory=list)
    screenshot_path: Path = field(default_factory=lambda: Path(os.devnull))
    prev_fingerprint: str = ""


@dataclass
class JobRegistry:
    """The control-plane job registry (BE-0198): the in-flight ``jobs`` dict, the monotonic id
    sequence, and the concurrency-cap enforcement, carved out of `ServeState` so the atomic
    "count-then-insert under one lock" invariant is expressed by this type's boundary rather than by
    prose on a docstring of the shared state. The registry is the sole owner of the id counter and of
    its own lock; ``logbus`` — the live-log channel wired onto each registered job (BE-0015) — is its
    only external dependency. The concurrency caps are configuration, not registry state, so
    `try_register` receives them per call rather than holding them."""

    logbus: LogBus
    jobs: dict[str, Job] = field(default_factory=dict)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def active_jobs(self) -> int:
        """How many spawned jobs are still running (not yet finished)."""
        with self._lock:
            return sum(1 for j in self.jobs.values() if j.status == "running")

    def in_flight_by_org(self) -> dict[str, int]:
        """Running jobs grouped by org, for the ``/metrics`` endpoint (BE-0169). Counted under the
        lock, like `active_jobs`, so a concurrent register/finish can't corrupt the snapshot."""
        with self._lock:
            return dict(Counter(j.org for j in self.jobs.values() if j.status == "running"))

    def register(self, job: Job) -> Job:
        with self._lock:
            return self._register(job)

    def try_register(
        self,
        job: Job,
        *,
        max_concurrent: int = 0,
        max_concurrent_per_user: int = 0,
        max_concurrent_per_org: int = 0,
    ) -> Job | None:
        """Register *job* only if under the concurrency caps, counting and inserting atomically under
        the lock so two concurrent dispatches can't both slip past a cap (BE-0051). Returns None at
        the global cap, at the per-user cap for an identified ``job.actor`` (BE-0015 7c-3), or at the
        per-org cap for ``job.org`` (BE-0016 Tier B pool fairness). Each cap ``<= 0`` is unlimited."""
        with self._lock:
            running = [j for j in self.jobs.values() if j.status == "running"]
            if max_concurrent > 0 and len(running) >= max_concurrent:
                return None
            if job.actor and max_concurrent_per_user > 0:
                mine = sum(1 for j in running if j.actor == job.actor)
                if mine >= max_concurrent_per_user:
                    return None
            if max_concurrent_per_org > 0:
                same_org = sum(1 for j in running if j.org == job.org)
                if same_org >= max_concurrent_per_org:
                    return None
            return self._register(job)

    def _register(self, job: Job) -> Job:
        """Assign the job its id + live-log bus and store it. Caller must hold ``self._lock``. The
        caller builds a fresh `Job` (the dataclass is the single source of truth for its fields), so
        adding a field never touches this layer. Single-use: registering a job that already has an id
        is a programming error (it would orphan the earlier ``jobs`` entry)."""
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


@dataclass
class ServeState:
    runs_dir: Path
    config: Path | None = None  # None until a config is opened from the UI
    scenarios_dir: Path | None = None  # override; default is the selected app's configured dir
    root: Path = field(default_factory=Path.cwd)  # the file browser's browse ceiling
    # where `visual` baselines live (and where Approve promotes to); serve() defaults it to
    # <scenarios_dir>/baselines.
    baselines_dir: Path = field(default_factory=lambda: Path("baselines"))
    # Sandbox for uploaded bundles (BE-0073): each upload extracts into its own dir here, a sibling
    # of runs_dir, never the browse `--root`, so an uploaded tree can't overwrite the operator's
    # files. serve() defaults it to a sibling of runs_dir.
    uploads_dir: Path = field(default_factory=lambda: Path("uploads"))
    # Drop-in theme directory (BE-0191 unit 2): scanned once at startup, its `*.css` folded into the
    # inlined theme stylesheet. None (the default / no `--themes`) means only the built-in themes.
    themes_dir: Path | None = None
    # The `ui.default_theme` initial selection, read from the startup config; None follows the OS.
    default_theme: str | None = None
    cwd: Path = field(default_factory=Path.cwd)
    # serve's launch directory, captured at construction (see __post_init__) before a config bind can
    # repoint `cwd`. Runs off a Git/upload bind still land their tree here (BE-0063/BE-0073).
    base_cwd: Path = field(init=False)
    # The currently bound uploaded bundle (BE-0073), or None when the active config came from the
    # file browser / Git / startup. Holds the extraction sandbox (removed when another config is
    # bound) and the run provenance. Only one bundle is bound at a time.
    upload: Upload | None = None
    # Policy for an uploaded bundle's launchServer command (and the latent mockServer.cmd, once it is
    # wired) (BE-0090): deny | reuse | sandbox. Default `sandbox` runs it in a throwaway container,
    # never on the serve host; it applies only to upload-sourced configs (a local/Git config is
    # operator-trusted and ungoverned). serve() sets it from --upload-exec / BAJUTSU_UPLOAD_EXEC.
    upload_exec: str = "sandbox"
    # Host-header allowlist (BE-0121): the hostnames a request's `Host` may name, set by
    # `make_server` from the bound interface. Empty — a wildcard bind, whose reachable names can't be
    # enumerated — disables the check; a loopback/named bind enforces its own names, closing the
    # DNS-rebinding path to endpoints like /api/apikey.
    allowed_hosts: frozenset[str] = frozenset()
    # Whether the active config is a Git source bound at runtime via the API (BE-0121), rather than
    # one the operator pre-configured at startup. An API-bound Git config is untrusted: its `build:`
    # command is nulled like an uploaded bundle's (never run) unless `allow_remote_build` opts in.
    git_config_from_api: bool = False
    # Git-source provenance of the active config when it came from a Git source (host/owner/repo/ref/
    # resolved sha, `config_source.source_provenance`), else None for a local file or uploaded bundle.
    # Surfaced by `/api/config/content` so the UI can show *which* commit the opaque cache-path config
    # was materialized from, not just the path. Set at startup (Git `--config`) and on an API bind.
    config_provenance: dict[str, str] | None = None
    # Opt-in to run an API-bound Git config's `build:` command on the host (BE-0121). Off by default;
    # serve() sets it from --allow-remote-build / BAJUTSU_ALLOW_REMOTE_BUILD.
    allow_remote_build: bool = False
    # Whether this is a hosted deployment (the server backend), the single source of truth for
    # deployment-aware config sourcing (BE-0108). The server backend sets it True where it wires its
    # hosted seams; the local backend (stdlib serve, including a self-hosted single Mac) never does,
    # so the file browser stays offered locally and is removed — UI and server-side — when hosted.
    hosted: bool = False
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
    # Operator secrets (the Claude API key today). Write-once: set/describe only, no plaintext read
    # an HTTP handler can reach (BE-0136). Default holds the value in the process env (in memory, as
    # before); a server backend with a database swaps in an encrypted per-org store.
    secrets: SecretStore = field(init=False)
    # The system of record (BE-0015 7a). None until a database is wired: local never has one, and a
    # server backend assigns a SqlRepository only when BAJUTSU_DATABASE_URL is set, so behavior is
    # unchanged without one. Annotated as a string (lazy) so the default path never loads SQLAlchemy.
    repository: Repository | None = None
    simctl: _simctl.RunFn = (
        _simctl._real_run
    )  # runs `xcrun simctl …` (booting devices, listing them)
    # Cap on concurrently-running run/record jobs so one caller can't monopolize the scarce device
    # (BE-0051). <= 0 means unlimited; serve() sets it from --max-concurrent-runs (default 4).
    max_concurrent: int = 4
    # Per-user cap on concurrent jobs (BE-0015 7c-3), so one OAuth user can't starve the pool. <= 0
    # means unlimited (the default); a server backend sets it from BAJUTSU_MAX_CONCURRENT_PER_USER.
    # Applies only to jobs that carry an actor (an OAuth identity); token/anonymous jobs are exempt.
    max_concurrent_per_user: int = 0
    # Per-org cap on concurrent jobs (BE-0016 Tier B pool fairness), so one tenant can't monopolize
    # the scarce Mac pool even when its users each stay under the per-user cap. <= 0 = unlimited (the
    # default), so a single-tenant deploy (every job in the default org) is unchanged; a server
    # backend sets it from BAJUTSU_MAX_CONCURRENT_PER_ORG. Every job carries an org, so this needs no
    # exemption — an operator opts in only when running multiple orgs.
    max_concurrent_per_org: int = 0
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
    capture: CaptureSession | None = None
    # Per-provider AI settings the Settings panel reads/writes (BE-0183), keyed by provider name.
    # Holds each provider's own model/effort/region so switching the dropdown stops discarding the
    # one left behind; the active provider's slot is materialized into the env vars spawned jobs read.
    # In memory, but flushed to `provider_settings_store` on save and reloaded on boot (BE-0184).
    provider_settings: dict[str, ProviderSettings] = field(default_factory=dict)
    # Durable backing for `provider_settings` + the active provider choice (BE-0184). Set on local
    # serve to a file-backed store so a saved choice survives a restart; None leaves the pre-BE-0184
    # session-only behavior (a hosted deployment resolves these per process, not per org, so it does
    # not wire a store yet). `set_provider` flushes here on save; boot restores from it.
    provider_settings_store: ProviderSettingsStore | None = None
    # The in-flight `ant auth login` subprocess (BE-0175), or None when no sign-in is running. Held
    # between the POST that starts it and the GET that polls it, so a second click doesn't spawn a
    # duplicate. Local serve only — a hosted deployment refuses the operation, so this stays None
    # there. Spawned through `popen` (the injectable seam above) so tests never exec the real CLI.
    # `ant_login_lock` makes the check-terminate-spawn sequence atomic: serve is a ThreadingHTTPServer,
    # so two concurrent POSTs must not both see None and each spawn a CLI (a leaked, unsupersedable proc).
    ant_login_proc: Any = None
    ant_login_lock: threading.Lock = field(default_factory=threading.Lock)
    # Where completed runs' evidence is uploaded (BE-0110). None = no evidence store configured (the
    # default; the upload-urls endpoint then hands back no URLs). serve() builds it from
    # --evidence-store / BAJUTSU_EVIDENCE_STORE; the server holds the credentials so a worker uploads
    # via presigned PUT URLs without any of its own.
    evidence: EvidenceTarget | None = None
    # The hosted object store + tenant base prefix the control plane signs worker upload/download
    # URLs against (BE-0160): the worker holds no cloud credentials, so it asks for a presigned URL
    # per file and reads/writes over plain HTTP. None/"" on local serve (no remote worker) — the
    # worker signing endpoints and the lease then return no URLs, like `evidence` when unset. A
    # server backend sets both where it wires its per-org object stores.
    object_store: ObjectStore | None = None
    object_store_prefix: str = ""
    # The job registry (BE-0198): owns the in-flight jobs, the id sequence, and the concurrency-cap
    # enforcement. Built in __post_init__ once `logbus` is resolved; `ServeState` forwards the
    # registration/counting surface to it and exposes `jobs` as a read-through of its dict.
    job_registry: JobRegistry = field(init=False)
    # Guards the `provider_settings` dict against concurrent Settings-panel reads/writes (serve is a
    # ThreadingHTTPServer). Named for what it protects — the job registry carries its own lock.
    _provider_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # serve's own launch directory, captured before any config bind repoints `cwd` at a Git
        # checkout / uploaded bundle. A run off such a bind writes its tree into `base_cwd/runs_dir`
        # (serve's store), not under the transient checkout/bundle (BE-0063/BE-0073).
        self.base_cwd = self.cwd
        # `artifacts`/`scenarios` are init=False so existing ServeState(...) calls don't change;
        # default them to the local stores here (a server backend overwrites them afterwards).
        self.artifacts = LocalArtifactStore(self.runs_dir)
        # Resolve the dir lazily through a closure so a config opened from the UI later is reflected.
        self.scenarios = LocalScenarioStore(lambda target: _scenarios_dir_for(self, target))
        self.baselines = LocalBaselineStore(self.baselines_dir)
        # The local secret store holds the value in this process's env; the name->env-var mapping is
        # resolved lazily so a config bound later (its `ai.keyEnv`, BE-0097) is reflected.
        self.secrets = EnvSecretStore(self._env_var_for_secret)
        # `logbus` is resolved by now (a plain field), so the registry can capture it: it is never
        # reassigned after construction, so the registry's reference stays the live bus.
        self.job_registry = JobRegistry(logbus=self.logbus)

    @property
    def jobs(self) -> dict[str, Job]:
        """The in-flight jobs, read through to the registry (BE-0198). Kept so existing lookups
        (`state.jobs.get(id)`) read unchanged now that the registry owns the dict. Treat it as
        read-only: register a job through `register` / `try_register` so its id assignment and cap
        check run under the registry's lock — inserting here directly bypasses that enforcement."""
        return self.job_registry.jobs

    def org_of(self, actor: str | None) -> str:
        """The org of *actor*, read from their persisted user row (assigned at login). The single
        `default` org without a database, without an identity, or for an unknown user (BE-0015)."""
        if self.repository is None or not actor:
            return _DEFAULT_ORG
        return self.repository.user_org(actor) or _DEFAULT_ORG

    def _env_var_for_secret(self, name: str) -> str:
        """The env var the local secret store reads/writes for logical secret *name* (BE-0136).

        The Claude API key honors the bound config's ``ai.keyEnv`` (BE-0097); the `claude-code`
        provider's OAuth token maps to its fixed CLI variable (BE-0215) — the `claude` CLI names it,
        so it is not config-overridable like the SDK key. Imported lazily to avoid a cycle with the
        operations layer, which imports this module."""
        from bajutsu.ai.claude_code import OAUTH_TOKEN_ENV
        from bajutsu.serve.operations.config import AI_CLAUDE_CODE_TOKEN_SECRET, active_key_env

        if name == AI_CLAUDE_CODE_TOKEN_SECRET:
            return OAUTH_TOKEN_ENV
        return active_key_env(self)

    def for_org(self, org: str) -> StoreBundle:
        """The storage seams scoped to *org*. A server backend prefixes each org's objects; local
        serve has a single tenant, so this is just the default stores (BE-0015 multi-tenancy)."""
        if self.org_stores is not None:
            return self.org_stores(org)
        return StoreBundle(self.artifacts, self.scenarios, self.baselines, self.secrets)

    def check_token(self, candidate: str) -> bool:
        """Constant-time compare of a presented token against the configured one."""
        return self.token is not None and secrets.compare_digest(candidate, self.token)

    def issue_session(self, identity: str | None = None) -> str:
        """Mint and remember a new opaque session id (returned to set as a cookie at login),
        optionally bound to *identity* (the GitHub login from an OAuth login)."""
        return self.sessions.issue(identity)

    def valid_session(self, sid: str) -> bool:
        return self.sessions.valid(sid)

    def provider_settings_snapshot(self) -> dict[str, ProviderSettings]:
        """A shallow copy of the per-provider AI settings, taken under the lock (BE-0183). serve is a
        ThreadingHTTPServer, so a bare `dict(...)` here could race a concurrent `set_provider_setting`
        write and raise "dictionary changed size during iteration"; the lock makes the snapshot safe."""
        with self._provider_lock:
            return dict(self.provider_settings)

    def set_provider_setting(self, name: str, settings: ProviderSettings) -> None:
        """Store one provider's AI settings slot under the lock (BE-0183), so a write can't corrupt a
        concurrent `provider_settings_snapshot` read on another request thread."""
        with self._provider_lock:
            self.provider_settings[name] = settings

    def set_provider_setting_and_snapshot(
        self, name: str, settings: ProviderSettings
    ) -> dict[str, ProviderSettings]:
        """Set one provider's slot and return a full snapshot, atomically under one lock acquisition.

        Used by the persistence flush path (BE-0184): taking the snapshot in the same lock scope as
        the write means the snapshot handed to `store.save` is always consistent with the just-applied
        change, and two concurrent saves can't interleave their snapshot-and-write sequences.
        """
        with self._provider_lock:
            self.provider_settings[name] = settings
            return dict(self.provider_settings)

    def active_jobs(self) -> int:
        """How many spawned jobs are still running (not yet finished). Delegates to the registry."""
        return self.job_registry.active_jobs()

    def in_flight_by_org(self) -> dict[str, int]:
        """Running jobs grouped by org, for the ``/metrics`` endpoint (BE-0169). Delegates to the
        registry."""
        return self.job_registry.in_flight_by_org()

    def register(self, job: Job) -> Job:
        """Assign *job* its id + live-log bus and store it. Delegates to the registry (BE-0198)."""
        return self.job_registry.register(job)

    def try_register(self, job: Job) -> Job | None:
        """Register *job* only if under the concurrency caps, forwarding this state's configured caps
        to the registry, which counts and inserts atomically under one lock (BE-0051)."""
        return self.job_registry.try_register(
            job,
            max_concurrent=self.max_concurrent,
            max_concurrent_per_user=self.max_concurrent_per_user,
            max_concurrent_per_org=self.max_concurrent_per_org,
        )

    def bind_upload(self, upload: Upload) -> None:
        """Make *upload* the active config (BE-0073): release any previously bound bundle's sandbox,
        then point `config`/`cwd` at this one so runs/record/crawl resolve from the extracted tree."""
        self.release_upload()
        self.upload = upload
        self.config = upload.config
        self.cwd = upload.root
        self.config_provenance = None  # a bundle is not a Git source — no commit provenance to show
        self.git_config_from_api = (
            False  # a bundle is governed by upload_exec, not the Git trust flag
        )

    def release_upload(self) -> None:
        """Drop the currently bound bundle's sandbox, if any, and reset `cwd` to serve's launch
        directory. Called whenever a new config is bound (from any source), so only one bundle is
        ever materialized and the file-browser/Git sources don't inherit a stale bundle cwd."""
        if self.upload is not None:
            shutil.rmtree(self.upload.dir, ignore_errors=True)
            self.upload = None
        self.cwd = self.base_cwd


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

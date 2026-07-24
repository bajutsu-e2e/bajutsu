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
import subprocess
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bajutsu.serve.project_registry import ProjectRegistry
    from bajutsu.serve.provider_store import ProviderSettingsStore
    from bajutsu.serve.server.db import Repository
    from bajutsu.serve.server.oauth import OAuthClient

from bajutsu import simctl as _simctl
from bajutsu.drivers import base as driver_base
from bajutsu.evidence.redaction import Redactor
from bajutsu.object_store import EvidenceTarget, ObjectStore
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
    # Capability tokens the worker running this job must advertise (BE-0166): its platform axis
    # (`platform:ios` / `platform:web`) plus the target's operator-declared `requires` (`ios18`,
    # `ipad`). Travels in the job spec so the hosted router leases it only to a capable worker; empty
    # for a local run (one worker, no routing).
    capabilities: list[str] = field(default_factory=list)
    # The project this run belongs to, resolved from the active project when the run is enqueued
    # (BE-0225 unit 3). Travels in the job spec so a remote worker's `_persist_run` stamps
    # `runs.project_id` without a registry of its own — a run started for project A stays labeled A
    # even if the active project changed before it finished. None when no project hub is wired.
    project_id: str | None = None
    # The requesting org's resolved AI provider env (BE-0229): provider/model/effort/language, merged
    # onto the spawn's inherited env by `_spawn_env` so the job uses *this* org's selection without
    # the serve process ever mutating its shared `os.environ` — the tenant-isolation guarantee.
    # Resolved at enqueue on the control plane (from the org's settings) and carried in the job spec,
    # so a remote worker needs no settings of its own. Empty when no provider is selected (the
    # zero-config path, BE-0101, then falls back to the job's inherited env unchanged).
    env_overlay: dict[str, str] = field(default_factory=dict)

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
    # The org's durable AI provider settings (BE-0229): the per-organization, DB-backed store on a
    # hosted deployment, the single file-backed store on local serve. None when persistence is not
    # wired (a server backend without a database) — the selection is then session-only in-memory,
    # the pre-BE-0184 shape. Read/written through `for_org(org)` like the other per-tenant seams.
    provider_settings: ProviderSettingsStore | None = None


@dataclass
class ProviderSettings:
    """One AI provider's remembered model/effort/region for the serve session (BE-0183).

    Scopes the fields to the provider they belong to, so switching the Settings dropdown no longer
    overwrites what was set for the provider left behind. `region` applies to `bedrock` only; the
    SDK/CLI providers leave it empty. Held in memory and materialized into env vars; on local serve
    it is also persisted through `ProviderSettingsManager.store` so a saved choice survives a restart
    (BE-0184).
    """

    model: str = ""
    effort: str = ""
    region: str = ""


@dataclass
class OrgProviderSettings:
    """One organization's AI provider selection (BE-0229): the active provider, its per-provider
    model/effort/region slots (BE-0183), and the output language (BE-0188).

    Replaces the single process-global selection with a per-org one, so a hosted multi-tenant serve
    resolves provider/model/effort per organization — whoever saved last no longer wins for everyone.
    `slots` maps a provider name to its remembered `ProviderSettings`; `provider` is the active one
    (empty = none selected, so resolution falls back to the launch env / default). `language` is the
    org-wide output-language override; blank/`auto` means the no-override default. Held in memory
    (keyed by org on `ServeState`) and, on a wired deployment, backed by the org's persistent store.
    """

    provider: str = ""
    slots: dict[str, ProviderSettings] = field(default_factory=dict)
    language: str = ""


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
    # Releases whatever backs `driver` when the session ends — for XCUITest the `xcodebuild` runner
    # subprocess, which dropping the session would otherwise leak (BE-0290). Default is a no-op so a
    # session built without one (older callers, tests) is still safe to close.
    teardown: Callable[[], None] = field(default=lambda: None)


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
class SessionManager:
    """The authentication cluster carved out of `ServeState` (BE-0248): the shared token, the login
    sessions, and the GitHub OAuth configuration, plus the "is this request authenticated, and as
    whom" methods that read them. Grouping them behind one boundary answers that question in one
    place, exactly as `JobRegistry` (BE-0198) did for job registration.

    `token` is the optional shared token (None = open, the loopback-only legacy behavior; once
    `oauth` is set it narrows to worker traffic, BE-0313); a login exchanges it for an opaque session
    id held by `sessions` — the token itself never lives in the browser. `sessions` is a swappable
    `SessionStore` seam (in-memory by default; a server backend swaps in a Redis/SQL store, BE-0015
    7b). `oauth` is the GitHub OAuth client (None = OAuth not configured); sign-in and the
    viewer/editor role then follow GitHub org and Team membership (`authz.py`, BE-0313), and
    `oauth_admin_team` is the one server-wide GitHub Team (`"<github-org>/<team-slug>"`) whose members
    are admin. The OAuth fields are fixed at server construction and never change after, so they
    travel with the token/session state they gate.
    """

    token: str | None = None
    sessions: SessionStore = field(default_factory=InMemorySessionStore)
    oauth: OAuthClient | None = None
    oauth_admin_team: str | None = None

    def check_token(self, candidate: str) -> bool:
        """Constant-time compare of a presented token against the configured one."""
        return self.token is not None and secrets.compare_digest(candidate, self.token)

    def issue_session(self, identity: str | None = None) -> str:
        """Mint and remember a new opaque session id (returned to set as a cookie at login),
        optionally bound to *identity* (the GitHub login from an OAuth login)."""
        return self.sessions.issue(identity)

    def valid_session(self, sid: str) -> bool:
        """Whether *sid* is a known, live session id."""
        return self.sessions.valid(sid)


@dataclass
class ProviderSettingsManager:
    """The per-org AI-provider-settings cluster carved out of `ServeState` (BE-0248): the in-memory
    selection map, its durable store, the two locks that guard them, and the read/write methods —
    the in-memory half of the per-org provider selection (BE-0229). Giving it a boundary makes the
    copy-on-read/copy-on-write discipline a property of this type rather than a convention spread
    across three method bodies, the way `JobRegistry` (BE-0198) made atomic id assignment a property
    of its boundary.

    `settings` maps an org to its `OrgProviderSettings`; `store` is the `default` org's durable
    backing on local serve (None on a hosted deployment, whose per-org stores come from `org_stores`,
    and on a server backend without a database — the selection is then session-only). `_provider_lock`
    guards the in-memory map against concurrent Settings-panel reads/writes (serve is a
    ThreadingHTTPServer); `_persist_lock` serializes the re-snapshot + disk write in `persist`, kept
    separate so I/O never runs inside the in-memory lock.
    """

    settings: dict[str, OrgProviderSettings] = field(default_factory=dict)
    store: ProviderSettingsStore | None = None
    _provider_lock: threading.Lock = field(default_factory=threading.Lock)
    _persist_lock: threading.Lock = field(default_factory=threading.Lock)

    def org_provider_settings(self, org: str) -> OrgProviderSettings | None:
        """A copy of *org*'s AI provider selection, or None when the org has no in-memory entry yet
        (BE-0229). Taken under the lock — serve is a ThreadingHTTPServer, so a bare read could race a
        concurrent `set_org_provider_choice` write. Returns a copy (the slots dict too) so the caller
        can never mutate the live entry. None means "not loaded"; the operations layer lazily loads
        it from the org's store on first access."""
        with self._provider_lock:
            current = self.settings.get(org)
            if current is None:
                return None
            return OrgProviderSettings(
                provider=current.provider,
                slots=dict(current.slots),
                language=current.language,
            )

    def put_org_provider_settings(self, org: str, settings: OrgProviderSettings) -> None:
        """Seed *org*'s in-memory entry from a freshly loaded snapshot (BE-0229), under the lock.
        Stores an independent copy so a later store reload can't alias a live entry."""
        with self._provider_lock:
            self.settings[org] = OrgProviderSettings(
                provider=settings.provider,
                slots=dict(settings.slots),
                language=settings.language,
            )

    def set_org_provider_choice(
        self, org: str, *, provider: str, slot: ProviderSettings, language: str
    ) -> None:
        """Apply one save to *org*'s selection under the lock (BE-0229): set the active *provider*,
        store its *slot* (BE-0183), and set the org-wide output *language* (BE-0188). The slot is
        written into the existing entry in place, so a provider left behind keeps its remembered slot
        — and a concurrent save for a *different* provider adds its own slot rather than clobbering
        this one (mirroring the pre-BE-0229 per-key map write). The active provider and language are
        last-writer-wins, as they were process-globally. Assumes the org's persisted slots are
        already loaded (the caller loads them first) so this never drops them."""
        with self._provider_lock:
            current = self.settings.get(org)
            if current is None:
                current = OrgProviderSettings()
                self.settings[org] = current
            current.slots[provider] = slot
            current.provider = provider
            current.language = language

    def persist(self, org: str, provider: str, store: ProviderSettingsStore) -> None:
        """Write *provider* + *org*'s current in-memory slot map to *store* (BE-0229), serialized by
        `_persist_lock` so whichever thread wins the lock last re-reads the org's settings inside it
        and writes the most up-to-date map. Keeping the lock inside this method is what lets the one
        out-of-package caller (`operations/config.py`'s `_persist_provider_settings`) drive the write
        without reaching into the manager's locks directly. Store resolution, failure handling, and
        the persisted/not-persisted signaling stay with that caller — this owns only the race-safe
        re-snapshot and the write."""
        # Imported lazily: `provider_store` imports `ProviderSettings` from this module, so a
        # top-level import here would be a cycle (the same reason `_env_var_for_secret` imports late).
        from bajutsu.serve.provider_store import PersistedProviderSettings

        with self._persist_lock:
            # Re-read inside the lock so the thread that wins last always writes the most recent
            # in-memory state, regardless of when each thread's mutation was applied.
            snapshot = self.org_provider_settings(org)
            slots = snapshot.slots if snapshot is not None else {}
            store.save(PersistedProviderSettings(provider=provider, settings=slots))


@dataclass
class ServeState:
    runs_dir: Path
    config: Path | None = None  # None until a config is opened from the UI
    scenarios_dir: Path | None = None  # override; default is the selected app's configured dir
    root: Path = field(default_factory=Path.cwd)  # the file browser's browse ceiling
    # where `visual` baselines live (and where Approve promotes to); serve() defaults it to
    # <scenarios_dir>/baselines.
    baselines_dir: Path = field(default_factory=lambda: Path("baselines"))
    # Root for uploaded-bundle extraction (BE-0073), never the browse `--root`, so an uploaded tree
    # can't overwrite the operator's files. Each org's entries live under their own sub-path, keyed
    # by content sha256 (BE-0243) — a durable, reusable cache in front of the object store, not a
    # disposable per-bind sandbox. serve() defaults it onto the shared `~/.cache/bajutsu/` root.
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
    # The project registry seam (BE-0225): list/switch the configs this serve holds and partition
    # runs by project. Local serve wires a JSON-file-backed store; a server backend with a database
    # wires the DB-backed one, else the same local JSON store. None leaves the single-config behavior
    # unchanged (no hub) — set in `_build_state`/`_build_server_state`, like the seams above.
    project_registry: ProjectRegistry | None = None
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
    # Authentication cluster (BE-0248): the shared token, the login sessions, and the GitHub OAuth
    # configuration + the "authenticated as whom" methods, carved into `SessionManager` (BE-0051 /
    # BE-0015 7b). `ServeState` holds one and the transport/authz layers read through `state.auth`.
    auth: SessionManager = field(default_factory=SessionManager)
    # Per-org store factory (BE-0015 multi-tenancy). None on local serve (one tenant); a server
    # backend sets a closure that builds object stores prefixed for the given org. `for_org` falls
    # back to the default stores when unset, so local behavior is unchanged.
    org_stores: Callable[[str], StoreBundle] | None = None
    capture: CaptureSession | None = None
    # Per-org AI provider settings (BE-0229), carved into `ProviderSettingsManager` (BE-0248): the
    # in-memory selection map, its durable local store, and the copy-on-read/copy-on-write methods
    # the Settings panel reads/writes. `ServeState` holds one and the operations layer reaches it as
    # `state.providers`; `for_org(default)` exposes its `store` as the bundle's `provider_settings`
    # seam so the operations layer reads/writes it uniformly with the hosted per-org store.
    providers: ProviderSettingsManager = field(default_factory=ProviderSettingsManager)
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
    # Days a soft-deleted run stays in the trash before the lazy sweep purges it (BE-0239). The
    # retention window that makes soft-delete non-instantly-destructive. <= 0 disables the automatic
    # purge (trash is kept until a manual purge). serve() sets it from BAJUTSU_RUN_RETENTION_DAYS.
    run_retention_days: int = 30
    # The job registry (BE-0198): owns the in-flight jobs, the id sequence, and the concurrency-cap
    # enforcement. Built in __post_init__ once `logbus` is resolved; `ServeState` forwards the
    # registration/counting surface to it and exposes `jobs` as a read-through of its dict.
    job_registry: JobRegistry = field(init=False)

    def __post_init__(self) -> None:
        # serve's own launch directory, captured before any config bind repoints `cwd` at a Git
        # checkout / uploaded bundle. A run off such a bind writes its tree into `base_cwd/runs_dir`
        # (serve's store), not under the transient checkout/bundle (BE-0063/BE-0073).
        self.base_cwd = self.cwd
        # Anchor a relative runs_dir / baselines_dir at serve's launch cwd (Path.cwd(), which serve
        # never changes) so each store, the run subprocess's `--runs-dir` / `--baselines`, and the
        # manifest reads in `jobs`/`triage` all resolve to one directory. Without this a subdir config
        # repoints `cwd` to the config's dir (BE-0242): the run then writes under `cwd/runs` while the
        # store reads `<launch>/runs`, so a just-finished replay's `report.html` — or a visual
        # baseline read/write — targets the wrong tree. An already-absolute dir (server/worker, an
        # explicit `--runs`/`--baselines`, tests) is left untouched.
        if not self.runs_dir.is_absolute():
            self.runs_dir = Path.cwd() / self.runs_dir
        if not self.baselines_dir.is_absolute():
            self.baselines_dir = Path.cwd() / self.baselines_dir
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
        so it is not config-overridable like the SDK key; the Git config-source credential maps to
        the bajutsu-owned ``BAJUTSU_GIT_CONFIG_TOKEN`` (BE-0224), which the in-process private-repo
        fetch reads — deliberately *not* ``GITHUB_TOKEN`` so clearing the UI credential never pops an
        operator's own exported token. Any other name is a scenario-declared secret (BE-0274): its
        `secrets:` entry already *is* an environment-variable name (BE-0032), so it maps to itself —
        not through ``active_key_env``, which would overwrite the AI key's var. Imported lazily to
        avoid a cycle with the operations layer, which imports this module."""
        from bajutsu.ai.claude_code import OAUTH_TOKEN_ENV
        from bajutsu.config_source import GIT_CONFIG_TOKEN_ENV
        from bajutsu.serve.operations.config import (
            AI_API_KEY_SECRET,
            AI_CLAUDE_CODE_TOKEN_SECRET,
            GIT_CONFIG_TOKEN_SECRET,
            active_key_env,
        )

        if name == AI_CLAUDE_CODE_TOKEN_SECRET:
            return OAUTH_TOKEN_ENV
        if name == GIT_CONFIG_TOKEN_SECRET:
            return GIT_CONFIG_TOKEN_ENV
        if name == AI_API_KEY_SECRET:
            return active_key_env(self)
        return name

    def for_org(self, org: str) -> StoreBundle:
        """The storage seams scoped to *org*. A server backend prefixes each org's objects; local
        serve has a single tenant, so this is just the default stores (BE-0015 multi-tenancy)."""
        if self.org_stores is not None:
            return self.org_stores(org)
        return StoreBundle(
            self.artifacts,
            self.scenarios,
            self.baselines,
            self.secrets,
            self.providers.store,
        )

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
        """Make *upload* the active binding (BE-0073): release any previously bound bundle, then
        point `config`/`cwd` at this one so runs/record/crawl resolve from the extracted tree."""
        self.release_upload()
        self.upload = upload
        self.config = upload.config
        self.cwd = upload.root
        self.config_provenance = None  # a bundle is not a Git source — no commit provenance to show
        self.git_config_from_api = (
            False  # a bundle is governed by upload_exec, not the Git trust flag
        )

    def release_upload(self) -> None:
        """Drop the currently bound bundle's binding, if any, and reset `cwd` to serve's launch
        directory. Called whenever a new config is bound (from any source), so the file-browser/Git
        sources don't inherit a stale bundle cwd. Unlike before BE-0243, this no longer removes
        `upload.dir`: it is now a sha256-keyed entry in `uploads_dir`'s shared extraction cache (a
        cache other binds, and other replicas via the object store, may still resolve to), not a
        disposable per-bind sandbox — its lifetime is independent of any single bind, the same way
        unbinding a Git-sourced config never sweeps that source's own checkout cache."""
        self.upload = None
        self.cwd = self.base_cwd


def _scenarios_dir_for(state: ServeState, target: str | None) -> Path | None:
    """The scenarios dir to list/save for *target*: the ``--scenarios`` override if set, else the
    target's configured dir.  None when neither is available.

    A configured dir is **relative to the config's base** — `state.cwd` — so a Git-sourced config
    (whose `cwd` is the checkout root) lists scenarios from the fetched tree, not serve's launch
    directory. A local config's `cwd` is its own directory too, so its scenarios resolve from beside
    the config file rather than from where serve was started (BE-0063, BE-0242)."""
    if state.scenarios_dir is not None:
        return state.scenarios_dir
    if state.config is None or not target:
        return None
    configured = target_scenarios_dir(state.config, target)
    if configured is None or configured.is_absolute():
        return configured
    return state.cwd / configured

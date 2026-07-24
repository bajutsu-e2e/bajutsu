"""`bajutsu serve` — a local web UI to author scenarios and run them.

A Tier-1 convenience (authoring / operation), **never part of the CI gate**.  Two top-level
tabs over the CLI: **Record** authors a scenario from a natural-language goal (``python -m
bajutsu record …``), streaming the agent's turn-by-turn progress and writing the result under
the scenarios dir; **Replay** runs a scenario (``python -m bajutsu run …``) and shows its
self-contained ``report.html``.  Each request spawns the CLI on a background thread, streams
its output, and the produced ``runs/<id>/`` tree is served so the report's relative asset
links resolve.  The default transport is stdlib-only — the same ``ThreadingHTTPServer`` approach
as the network collector ([[network]]); ``--asgi`` instead serves the same UI/API as a FastAPI app
over uvicorn (the ``server`` extra), the transport the hosted backend uses (BE-0015).

Split into submodules:

* **helpers** — pure query/path/validation functions (no server state)
* **commands** — CLI command builders (the ``python -m bajutsu …`` argv a request spawns)
* **state** — the ``ServeState`` container and its ``Job``/``StoreBundle`` value types
* **jobs** — the run/cancel/build execution lifecycle that mutates a ``Job``
* **handler** — HTTP request handler, ``make_server``, and the embedded SPA
"""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path
from typing import Any

from bajutsu.config_source import _bajutsu_cache_root
from bajutsu.object_store import EvidenceTarget
from bajutsu.serve import gate
from bajutsu.serve.artifacts import Artifact, ArtifactStore, LocalArtifactStore
from bajutsu.serve.commands import (
    _int,
    crawl_command,
    record_command,
    run_command,
    triage_command,
)
from bajutsu.serve.executor import LocalExecutor, RunExecutor
from bajutsu.serve.handler import make_server
from bajutsu.serve.helpers import (
    _scenario_path,
    list_crawl_runs,
    list_fs,
    list_runs,
    list_scenarios,
    list_simulators,
    list_targets,
    load_serve_config_file,
    mask_secret,
    parse_byte_range,
    range_reply,
    scenario_out_path,
    target_build_info,
    target_scenarios_dir,
    unique_scenario_path,
    valid_backend,
    valid_run_id,
    valid_udid,
)
from bajutsu.serve.jobs import cancel_job, run_job
from bajutsu.serve.launchagent import launchagent_plist
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.operations.config import (
    register_launch_project,
    restore_persisted_provider_settings,
)
from bajutsu.serve.orgs import DEFAULT_ORG, targets_for_org
from bajutsu.serve.project_registry import LocalProjectRegistry, SqlProjectRegistry
from bajutsu.serve.provider_store import LocalProviderSettingsStore
from bajutsu.serve.scenarios import (
    LocalScenarioScope,
    LocalScenarioStore,
    ScenarioScope,
    ScenarioStore,
)
from bajutsu.serve.secrets import SecretStore
from bajutsu.serve.sessions import InMemorySessionStore
from bajutsu.serve.state import (
    Job,
    Popen,
    ProviderSettingsManager,
    ServeState,
    SessionManager,
    StoreBundle,
)

__all__ = [
    "SERVE_BACKENDS",
    "Artifact",
    "ArtifactStore",
    "InMemoryLogBus",
    "Job",
    "LocalArtifactStore",
    "LocalExecutor",
    "LocalScenarioScope",
    "LocalScenarioStore",
    "LogBus",
    "MissingServerExtra",
    "Popen",
    "RunExecutor",
    "ScenarioScope",
    "ScenarioStore",
    "ServeState",
    "_int",
    "_scenario_path",
    "cancel_job",
    "crawl_command",
    "launchagent_plist",
    "list_crawl_runs",
    "list_fs",
    "list_runs",
    "list_scenarios",
    "list_simulators",
    "list_targets",
    "make_server",
    "mask_secret",
    "parse_byte_range",
    "range_reply",
    "record_command",
    "run_command",
    "run_job",
    "scenario_out_path",
    "serve",
    "target_build_info",
    "target_scenarios_dir",
    "triage_command",
    "unique_scenario_path",
    "valid_backend",
    "valid_run_id",
    "valid_udid",
]


# The serve backends `_build_state` can assemble — the source of truth the CLI validates against.
# `local` runs in-process; `server` wires the hosted seams (Redis queue/log bus + object storage).
SERVE_BACKENDS: tuple[str, ...] = ("local", "server")


def _session_ttl_from_env(raw: str | None, default: int) -> int:
    """Parse ``BAJUTSU_SESSION_TTL`` (seconds) defensively — operator-facing config deserves a clear
    error, not a bare ValueError. Unset/empty falls back to *default*; non-integer or non-positive
    values are rejected with a message naming the variable."""
    if not raw:
        return default
    try:
        ttl = int(raw)
    except ValueError:
        raise ValueError(
            f"BAJUTSU_SESSION_TTL must be a whole number of seconds, got {raw!r}"
        ) from None
    if ttl <= 0:
        raise ValueError(f"BAJUTSU_SESSION_TTL must be a positive number of seconds, got {ttl}")
    return ttl


def _max_concurrent_from_env(raw: str | None, *, var: str) -> int:
    """Parse a non-negative concurrency cap from *var* (unset/empty/0 = unlimited). A clear error
    beats a bare ValueError for operator-facing config; negatives and non-integers are rejected.
    Shared by the per-user (BE-0015 7c-3) and per-org (BE-0016 Tier B fairness) caps."""
    if not raw:
        return 0
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"{var} must be a whole number, got {raw!r}") from None
    if n < 0:
        raise ValueError(f"{var} must be >= 0, got {n}")
    return n


_DEFAULT_RUN_RETENTION_DAYS = 30


def _run_retention_from_env(raw: str | None) -> int:
    """Parse ``BAJUTSU_RUN_RETENTION_DAYS`` — days a soft-deleted run stays in the trash before the
    lazy sweep purges it (BE-0239). Unset/empty falls back to the 30-day default; a value <= 0
    disables the automatic purge (trash kept until a manual purge); a non-integer is rejected with a
    message naming the variable, like the other operator-facing knobs."""
    if not raw:
        return _DEFAULT_RUN_RETENTION_DAYS
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"BAJUTSU_RUN_RETENTION_DAYS must be a whole number of days, got {raw!r}"
        ) from None


class MissingServerExtra(ImportError):
    """A server-backend optional extra (Redis/RQ, object storage, the database) is not installed.

    Carries the install hint and lets the CLI exit cleanly. Distinct from a plain ImportError so a
    genuine `bajutsu.*` import bug is never mistaken for a missing dependency and swallowed."""


def _build_state(
    *,
    runs_dir: Path,
    config: Path | None,
    scenarios_dir: Path | None,
    root: Path | None,
    baselines_dir: Path | None,
    max_concurrent: int,
    token: str | None,
    upload_exec: str = "sandbox",
    evidence: EvidenceTarget | None = None,
    allow_remote_build: bool = False,
    backend: str = "local",
    cwd: Path | None = None,
    config_provenance: dict[str, str] | None = None,
    themes_dir: Path | None = None,
    default_theme: str | None = None,
) -> ServeState:
    """Assemble the `ServeState` for *backend* — the one place the serve seams are wired.

    ``local`` runs in-process (thread executor, in-memory log bus, filesystem artifacts, on-disk
    scenarios). ``server`` wires the hosted seams (Redis-backed queue + log bus, object-storage
    artifacts + scenarios) from the environment. An unknown backend fails loudly rather than
    silently falling back to local. The transport (stdlib vs uvicorn) is a separate choice."""
    if backend not in SERVE_BACKENDS:
        raise ValueError(
            f"unknown serve backend: {backend!r} (available: {', '.join(SERVE_BACKENDS)})"
        )
    resolved_baselines = baselines_dir or (
        scenarios_dir / "baselines" if scenarios_dir else Path("baselines")
    )
    if backend == "server":
        # The server backend's seams (Redis/RQ, object storage, the database) live behind the
        # optional extras and import lazily. If any is missing, surface one clear install hint
        # rather than a raw ImportError naming whichever module happened to load first.
        try:
            return _build_server_state(
                runs_dir=runs_dir,
                config=config,
                scenarios_dir=scenarios_dir,
                root=root or Path.cwd(),
                baselines_dir=resolved_baselines,
                max_concurrent=max_concurrent,
                token=token,
                upload_exec=upload_exec,
                evidence=evidence,
                allow_remote_build=allow_remote_build,
            )
        except ImportError as e:
            # Only a missing third-party extra earns the install hint. A failed `bajutsu.*` import is
            # a real bug, not a missing dependency — re-raise it so its own traceback survives.
            if e.name and e.name.split(".")[0] == "bajutsu":
                raise
            raise MissingServerExtra(
                "the server backend needs its optional extras — install with: "
                "pip install 'bajutsu[server,worker,db,gcs]' (gcs is only needed if "
                "BAJUTSU_SERVER_STORE or --evidence-store uses gs://)"
            ) from e
    return ServeState(
        runs_dir=runs_dir,
        config=config,
        scenarios_dir=scenarios_dir,
        root=root or Path.cwd(),
        baselines_dir=resolved_baselines,
        # Uploaded bundles (BE-0073) extract under the shared Bajutsu cache root — serve-owned,
        # never the browse `--root`, so an uploaded tree can't overwrite the operator's files. A
        # sibling of the Git source's own checkout cache (BE-0243), so a hosted deployment that
        # already provisions one writable cache root for Git needn't provision a second for uploads.
        uploads_dir=_bajutsu_cache_root() / "uploads",
        max_concurrent=max_concurrent,
        auth=SessionManager(token=token),
        upload_exec=upload_exec,
        evidence=evidence,
        allow_remote_build=allow_remote_build,
        run_retention_days=_run_retention_from_env(os.environ.get("BAJUTSU_RUN_RETENTION_DAYS")),
        cwd=cwd or Path.cwd(),
        config_provenance=config_provenance,
        themes_dir=themes_dir,
        default_theme=default_theme,
        # Persist the Settings panel's provider/model/effort to a serve-owned file (a sibling of
        # runs_dir), so a saved choice survives a restart (BE-0184). Construction only wires the
        # store; `serve()` restores from it on boot, once logging is live.
        providers=ProviderSettingsManager(
            store=LocalProviderSettingsStore(runs_dir.parent / "provider-settings.json")
        ),
        # The project hub's registry (BE-0225): local serve persists it to a JSON file beside
        # runs_dir, the local stand-in for the DB's projects/runs tables. serve() auto-registers the
        # launch config as the active project on boot, once logging is live.
        project_registry=LocalProjectRegistry(runs_dir.parent / "projects.json"),
    )


def _build_server_state(
    *,
    runs_dir: Path,
    config: Path | None,
    scenarios_dir: Path | None,
    root: Path,
    baselines_dir: Path,
    max_concurrent: int,
    token: str | None,
    upload_exec: str = "sandbox",
    evidence: EvidenceTarget | None = None,
    allow_remote_build: bool = False,
) -> ServeState:
    """Wire the hosted seams from the environment (the single-tenant server backend, BE-0015/BE-0106).

    Postgres (``BAJUTSU_DATABASE_URL``) backs the job queue, sessions, and the system of record
    (BE-0106); one bucket named by ``BAJUTSU_SERVER_STORE`` (``s3://bucket/prefix`` or
    ``gs://bucket/prefix``, BE-0204) holds artifacts and scenarios — the same URI-based selector
    ``--evidence-store`` uses, kept as its own independent setting. Server extras (SQLAlchemy,
    boto3, google-cloud-storage) are imported lazily so the default path stays SDK-free."""
    import os

    from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
    from bajutsu.serve.server.baselines import ObjectBaselineStore
    from bajutsu.serve.server.db import engine_from_url, repository_from_env
    from bajutsu.serve.server.db_executor import DbQueueExecutor
    from bajutsu.serve.server.oauth import GitHubOAuthClient
    from bajutsu.serve.server.object_store import artifact_prefix, object_store_from_env, org_prefix
    from bajutsu.serve.server.post_completion_logbus import PostCompletionLogBus
    from bajutsu.serve.server.provider_store import DbProviderSettingsStore
    from bajutsu.serve.server.scenarios import ObjectScenarioStorage, StorageScenarioStore
    from bajutsu.serve.server.secrets import DbSecretStore, fernet_from_env
    from bajutsu.serve.server.sessions import _DEFAULT_TTL, SqlSessionStore

    server_store = object_store_from_env()
    if server_store is None:
        raise ValueError(
            "BAJUTSU_SERVER_STORE is required for --backend=server "
            "(e.g. s3://bucket/prefix or gs://bucket/prefix)"
        )
    store, prefix = server_store
    db_url = os.environ.get("BAJUTSU_DATABASE_URL")
    _db_engine = engine_from_url(db_url) if db_url else None
    # The master key that encrypts operator secrets at rest (BE-0136). A database-backed server
    # persists secrets in the `secrets` table, so it must be provisioned — fail loudly at startup
    # rather than silently degrade to plaintext-in-memory. Without a database, secrets stay in the
    # process env (the local shape), so no key is needed.
    _secrets_fernet = fernet_from_env()
    if _db_engine is not None and _secrets_fernet is None:
        raise ValueError("BAJUTSU_SECRETS_KEY is required for --backend=server with a database")
    # GitHub OAuth login is optional: wired only when all three OAuth vars are set, else None (token
    # auth only). Once configured, sign-in and the viewer/editor role follow GitHub org/Team
    # membership (BE-0313); `BAJUTSU_OAUTH_ADMIN_TEAM` names the one server-wide admin Team.
    cid = os.environ.get("BAJUTSU_OAUTH_GITHUB_CLIENT_ID")
    secret = os.environ.get("BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET")
    redirect = os.environ.get("BAJUTSU_OAUTH_GITHUB_REDIRECT_URI")
    oauth = (
        GitHubOAuthClient(client_id=cid, client_secret=secret, redirect_uri=redirect)
        if cid and secret and redirect
        else None
    )
    oauth_admin_team = os.environ.get("BAJUTSU_OAUTH_ADMIN_TEAM") or None

    repo = repository_from_env()

    state = ServeState(
        runs_dir=runs_dir,
        config=config,
        scenarios_dir=scenarios_dir,
        root=root,
        baselines_dir=baselines_dir,
        max_concurrent=max_concurrent,
        max_concurrent_per_user=_max_concurrent_from_env(
            os.environ.get("BAJUTSU_MAX_CONCURRENT_PER_USER"),
            var="BAJUTSU_MAX_CONCURRENT_PER_USER",
        ),
        max_concurrent_per_org=_max_concurrent_from_env(
            os.environ.get("BAJUTSU_MAX_CONCURRENT_PER_ORG"),
            var="BAJUTSU_MAX_CONCURRENT_PER_ORG",
        ),
        upload_exec=upload_exec,
        evidence=evidence,
        # Uploaded bundles (BE-0073) extract under the shared Bajutsu cache root, a sibling of the
        # Git source's own checkout cache — the server backend previously left this at ServeState's
        # bare `Path("uploads")` default (BE-0243), never overriding it here.
        uploads_dir=_bajutsu_cache_root() / "uploads",
        # The hosted object store the control plane signs worker upload/download URLs against, and
        # its tenant base prefix (BE-0160). The worker never receives these credentials — only signed
        # URLs — so it needs no cloud SDK of its own.
        object_store=store,
        object_store_prefix=prefix,
        allow_remote_build=allow_remote_build,
        run_retention_days=_run_retention_from_env(os.environ.get("BAJUTSU_RUN_RETENTION_DAYS")),
        hosted=True,  # the server backend is a hosted deployment: drop the file browser (BE-0108)
        executor=DbQueueExecutor(repo) if repo is not None else LocalExecutor(),
        logbus=(
            PostCompletionLogBus(
                repo,
                artifacts_fn=lambda org: ObjectStorageArtifactStore(
                    store, prefix=artifact_prefix(org_prefix(prefix, org))
                ),
            )
            if repo is not None
            else InMemoryLogBus()
        ),
        repository=repo,
        # The project hub's registry (BE-0225): DB-backed when a database is wired, else the same
        # local JSON store the local backend uses — matching how the executor/logbus above fall back
        # to their in-process defaults when no repository is present.
        project_registry=(
            SqlProjectRegistry(repo)
            if repo is not None
            else LocalProjectRegistry(runs_dir.parent / "projects.json")
        ),
        # The authentication cluster (BE-0248): the shared token, the login-session store (a
        # DB-backed one when a database is wired so sessions survive restarts, else in-memory), and
        # the GitHub OAuth client + the server-wide admin Team (BE-0313).
        auth=SessionManager(
            token=token,
            sessions=(
                SqlSessionStore(
                    _db_engine,
                    ttl=_session_ttl_from_env(os.environ.get("BAJUTSU_SESSION_TTL"), _DEFAULT_TTL),
                )
                if _db_engine is not None
                else InMemorySessionStore()
            ),
            oauth=oauth,
            oauth_admin_team=oauth_admin_team,
        ),
    )

    # Build the object-storage seams per org (BE-0015 multi-tenancy): each org's artifacts/
    # scenarios/baselines live under its own key prefix, and its scenario store only acknowledges
    # the targets that org owns. The scenario targets are read from the live config, so a config
    # opened later is reflected.
    def _org_apps(org: str) -> list[str]:
        parsed = load_serve_config_file(state.config)
        return targets_for_org(parsed[1], parsed[0].targets, org) if parsed is not None else []

    def make_bundle(org: str) -> StoreBundle:
        base = org_prefix(prefix, org)
        # Secrets are encrypted per org in the database when one is wired; without a database the
        # server keeps the local shape (value in the process env), matching how the executor/logbus
        # fall back to their in-process defaults above (BE-0136). The startup guard above already
        # rejected "database wired but no master key", so the process-env fallback is only ever the
        # no-database case — asserted here so softening that guard can't silently turn this into a
        # plaintext-in-memory store for a database-backed deployment.
        if _db_engine is not None:
            assert _secrets_fernet is not None  # guaranteed by the BAJUTSU_SECRETS_KEY check above
            secrets: SecretStore = DbSecretStore(_db_engine, org, _secrets_fernet)
        else:
            secrets = state.secrets  # the process-env local store built in __post_init__
        # The per-org AI provider selection (BE-0229): DB-backed when a database is wired, so a saved
        # choice survives a restart per org; None without one, so the selection is session-only
        # in-memory (still resolved per org, just not persisted) — the same no-database fallback the
        # secret store takes above. Not sensitive, so it is stored in the clear.
        provider_settings = (
            DbProviderSettingsStore(_db_engine, org) if _db_engine is not None else None
        )
        return StoreBundle(
            artifacts=ObjectStorageArtifactStore(store, prefix=artifact_prefix(base)),
            scenarios=StorageScenarioStore(
                ObjectScenarioStorage(store, partial(_org_apps, org), prefix=base)
            ),
            baselines=ObjectBaselineStore(store, prefix=base),
            secrets=secrets,
            provider_settings=provider_settings,
        )

    state.org_stores = make_bundle
    # The default-org bundle backs the bare ServeState fields, so code paths that don't resolve an
    # org (and local-parity tests) keep working.
    default = make_bundle(DEFAULT_ORG)
    state.artifacts, state.scenarios, state.baselines, state.secrets = (
        default.artifacts,
        default.scenarios,
        default.baselines,
        default.secrets,
    )
    return state


def _configure_oplog(state: ServeState) -> None:
    """Install the operational-logging channel for a serve process (BE-0055).

    Serve defaults to structured JSON; the process-lifetime redactor is seeded with the secrets
    that exist at startup (operator token, OAuth client secret, API key) so they can never reach a
    log line. Per-run ``${secrets.X}`` values are masked separately, run-scoped, on the worker.
    """
    from bajutsu.serve import oplog

    static = (
        state.auth.token,
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("BAJUTSU_SERVE_TOKEN"),
        os.environ.get("BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET"),
    )
    oplog.configure(
        fmt=os.environ.get("BAJUTSU_LOG_FORMAT") or "json",
        level=os.environ.get("BAJUTSU_LOG_LEVEL") or "INFO",
        secrets=tuple(v for v in static if v),
    )


def make_asgi_server(state: ServeState, host: str = "127.0.0.1", port: int = 8765) -> Any:
    """A uvicorn ``Server`` running the FastAPI control-plane app over *state*. uvicorn and the app
    (FastAPI) are imported lazily — only when the ASGI transport is selected — so the default path
    and the import guard (#117) stay server-free. (Return type is Any so the module needn't import
    uvicorn to annotate it.)"""
    try:
        import uvicorn

        from bajutsu.serve.server.app import make_app
    except ImportError as e:
        raise ImportError(
            "the 'server' extra is required for --asgi (FastAPI + uvicorn) — "
            "install with: pip install 'bajutsu[server]'"
        ) from e

    # Derive the Host allowlist from the bound interface, exactly as make_server does for the stdlib
    # transport, so the ASGI gate enforces the same DNS-rebinding defense (BE-0121).
    state.allowed_hosts = gate.allowed_hosts(host)
    return uvicorn.Server(
        uvicorn.Config(make_app(state), host=host, port=port, log_level="warning")
    )


def serve(
    host: str,
    port: int,
    scenarios_dir: Path | None,
    config: Path | None,
    runs_dir: Path,
    root: Path | None = None,
    baselines_dir: Path | None = None,
    max_concurrent: int = 4,
    token: str | None = None,
    *,
    upload_exec: str = "sandbox",
    evidence: EvidenceTarget | None = None,
    allow_remote_build: bool = False,
    asgi: bool = False,
    backend: str = "local",
    cwd: Path | None = None,
    config_provenance: dict[str, str] | None = None,
    themes_dir: Path | None = None,
    default_theme: str | None = None,
) -> None:
    state = _build_state(
        runs_dir=runs_dir,
        config=config,
        scenarios_dir=scenarios_dir,
        root=root,
        baselines_dir=baselines_dir,
        max_concurrent=max_concurrent,
        token=token,
        upload_exec=upload_exec,
        evidence=evidence,
        allow_remote_build=allow_remote_build,
        backend=backend,
        cwd=cwd,
        config_provenance=config_provenance,
        themes_dir=themes_dir,
        default_theme=default_theme,
    )
    # No startup sweep of uploads_dir: every entry is now a content-addressed cache keyed by its
    # sha256 (BE-0243), as valid a cache hit moments after a restart as one extracted just before
    # it — the same reason nothing sweeps the Git source's own checkout cache at startup either.
    _configure_oplog(state)
    # Restore the operator's last-saved provider/model/effort before the first request, so a restart
    # reflects it rather than resetting to the launch environment (BE-0184). After `_configure_oplog`
    # so a malformed-file warning reaches the live log sink; a no-op when nothing is persisted (BE-0101).
    restore_persisted_provider_settings(state)
    # Auto-register the launch config as the active project (BE-0225), so a bare `serve --config X`
    # gains the project hub and its runs are attributed to X from the first one. After the provider
    # restore, sharing its boot placement; a no-op when no config is bound or no registry is wired.
    register_launch_project(state)
    hint = str(config) if config else "open a config.yml in the UI"
    if not gate.allowed_hosts(host):
        # A wildcard bind can't enumerate its reachable hostnames, so the Host allowlist is off
        # (BE-0121). Say so, rather than silently downgrading the DNS-rebinding defense — CSRF stays
        # the cross-origin guard, and a non-loopback bind already requires a token.
        print(  # noqa: T201
            f"note: Host-header enforcement is off for the wildcard bind {host!r}; "
            "CSRF remains the cross-origin defense (BE-0121)"
        )
    if asgi:
        # The FastAPI app over uvicorn — the transport the hosted backend will use; runnable now
        # with the local backend (a single-process ASGI server) so the path is exercised before
        # the hosted seams land.
        print(f"bajutsu serve (asgi) → http://{host}:{port}  (config: {hint} · Ctrl-C to stop)")  # noqa: T201
        make_asgi_server(state, host, port).run()
        return
    server = make_server(state, host, port)
    bound = server.server_address[1]
    print(f"bajutsu serve → http://{host}:{bound}  (config: {hint} · Ctrl-C to stop)")  # noqa: T201
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")  # noqa: T201
    finally:
        server.shutdown()
        server.server_close()

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

Split into three submodules:

* **helpers** — pure query/command-builder functions (no server state)
* **jobs** — ``Job``/``ServeState`` dataclasses and the run/cancel lifecycle
* **handler** — HTTP request handler, ``make_server``, and the embedded SPA
"""

from __future__ import annotations

import os
import shutil
from functools import partial
from pathlib import Path
from typing import Any

from bajutsu.object_store import EvidenceTarget
from bajutsu.serve.artifacts import Artifact, ArtifactStore, LocalArtifactStore
from bajutsu.serve.executor import LocalExecutor, RunExecutor
from bajutsu.serve.handler import _allowed_hosts, make_server
from bajutsu.serve.helpers import (
    _int,
    _scenario_path,
    crawl_command,
    list_fs,
    list_runs,
    list_scenarios,
    list_simulators,
    list_targets,
    load_serve_config_file,
    mask_secret,
    record_command,
    run_command,
    scenario_out_path,
    target_build_info,
    target_scenarios_dir,
    triage_command,
    unique_scenario_path,
    valid_backend,
    valid_run_id,
    valid_udid,
)
from bajutsu.serve.jobs import Job, Popen, ServeState, StoreBundle, cancel_job, run_job
from bajutsu.serve.launchagent import launchagent_plist
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.orgs import DEFAULT_ORG, targets_for_org
from bajutsu.serve.scenarios import (
    LocalScenarioScope,
    LocalScenarioStore,
    ScenarioScope,
    ScenarioStore,
)
from bajutsu.serve.secrets import SecretStore
from bajutsu.serve.sessions import InMemorySessionStore

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
    "list_fs",
    "list_runs",
    "list_scenarios",
    "list_simulators",
    "list_targets",
    "make_server",
    "mask_secret",
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
                "the server backend needs its optional extras — "
                "install with: pip install 'bajutsu[server,worker,db]'"
            ) from e
    return ServeState(
        runs_dir=runs_dir,
        config=config,
        scenarios_dir=scenarios_dir,
        root=root or Path.cwd(),
        baselines_dir=resolved_baselines,
        # Uploaded bundles (BE-0073) extract into a sibling of runs_dir — serve-owned, never the
        # browse `--root`, so an uploaded tree can't overwrite the operator's files.
        uploads_dir=runs_dir.parent / "uploads",
        max_concurrent=max_concurrent,
        token=token,
        upload_exec=upload_exec,
        evidence=evidence,
        allow_remote_build=allow_remote_build,
        cwd=cwd or Path.cwd(),
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
    (BE-0106); one S3-compatible bucket (``BAJUTSU_S3_BUCKET`` / ``BAJUTSU_S3_ENDPOINT`` /
    ``BAJUTSU_S3_REGION``, optional ``BAJUTSU_S3_PREFIX``) holds artifacts and scenarios. Server
    extras (SQLAlchemy, boto3) are imported lazily so the default path stays SDK-free."""
    import os

    from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
    from bajutsu.serve.server.baselines import ObjectBaselineStore
    from bajutsu.serve.server.db import engine_from_url, repository_from_env
    from bajutsu.serve.server.db_executor import DbQueueExecutor
    from bajutsu.serve.server.oauth import GitHubOAuthClient
    from bajutsu.serve.server.object_store import (
        artifact_prefix,
        object_store_from_env,
        org_prefix,
        s3_prefix,
    )
    from bajutsu.serve.server.post_completion_logbus import PostCompletionLogBus
    from bajutsu.serve.server.scenarios import ObjectScenarioStorage, StorageScenarioStore
    from bajutsu.serve.server.secrets import DbSecretStore, fernet_from_env
    from bajutsu.serve.server.sessions import _DEFAULT_TTL, SqlSessionStore

    store = object_store_from_env()
    if store is None:
        raise ValueError("BAJUTSU_S3_BUCKET is required for --backend=server")
    prefix = s3_prefix()
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
    # auth only). The allowlist is the GitHub logins permitted to log in (BE-0015 7b-2).
    cid = os.environ.get("BAJUTSU_OAUTH_GITHUB_CLIENT_ID")
    secret = os.environ.get("BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET")
    redirect = os.environ.get("BAJUTSU_OAUTH_GITHUB_REDIRECT_URI")
    oauth = (
        GitHubOAuthClient(client_id=cid, client_secret=secret, redirect_uri=redirect)
        if cid and secret and redirect
        else None
    )

    def _logins(var: str) -> frozenset[str]:
        return frozenset(u.strip() for u in os.environ.get(var, "").split(",") if u.strip())

    allowed_users = _logins("BAJUTSU_OAUTH_ALLOWED_USERS")
    oauth_admins = _logins("BAJUTSU_OAUTH_ADMINS")
    oauth_viewers = _logins("BAJUTSU_OAUTH_VIEWERS")

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
        token=token,
        upload_exec=upload_exec,
        evidence=evidence,
        # The hosted object store the control plane signs worker upload/download URLs against, and
        # its tenant base prefix (BE-0160). The worker never receives these credentials — only signed
        # URLs — so it needs no cloud SDK of its own.
        object_store=store,
        object_store_prefix=prefix,
        allow_remote_build=allow_remote_build,
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
        sessions=(
            SqlSessionStore(
                _db_engine,
                ttl=_session_ttl_from_env(os.environ.get("BAJUTSU_SESSION_TTL"), _DEFAULT_TTL),
            )
            if _db_engine is not None
            else InMemorySessionStore()
        ),
        repository=repo,
        oauth=oauth,
        oauth_allowed_users=allowed_users,
        oauth_admins=oauth_admins,
        oauth_viewers=oauth_viewers,
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
        return StoreBundle(
            artifacts=ObjectStorageArtifactStore(store, prefix=artifact_prefix(base)),
            scenarios=StorageScenarioStore(
                ObjectScenarioStorage(store, partial(_org_apps, org), prefix=base)
            ),
            baselines=ObjectBaselineStore(store, prefix=base),
            secrets=secrets,
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
        state.token,
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
    state.allowed_hosts = _allowed_hosts(host)
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
    )
    # Clear upload sandboxes orphaned by a prior serve process — the dir is serve-owned and
    # ephemeral, and nothing is bound at startup, so this just stops them accumulating across
    # restarts (BE-0073; a bound bundle is removed when another config is bound while running).
    shutil.rmtree(state.uploads_dir, ignore_errors=True)
    _configure_oplog(state)
    hint = str(config) if config else "open a config.yml in the UI"
    if not _allowed_hosts(host):
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

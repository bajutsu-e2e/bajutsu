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

from pathlib import Path
from typing import Any

from bajutsu.serve.artifacts import Artifact, ArtifactStore, LocalArtifactStore
from bajutsu.serve.executor import LocalExecutor, RunExecutor
from bajutsu.serve.handler import make_server
from bajutsu.serve.helpers import (
    _int,
    _scenario_path,
    app_build_info,
    app_scenarios_dir,
    crawl_command,
    list_apps,
    list_fs,
    list_runs,
    list_scenarios,
    list_simulators,
    mask_secret,
    record_command,
    run_command,
    scenario_out_path,
    unique_scenario_path,
    valid_backend,
    valid_run_id,
    valid_udid,
)
from bajutsu.serve.jobs import Job, Popen, ServeState, cancel_job, run_job
from bajutsu.serve.launchagent import launchagent_plist
from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.scenarios import (
    LocalScenarioScope,
    LocalScenarioStore,
    ScenarioScope,
    ScenarioStore,
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
    "Popen",
    "RunExecutor",
    "ScenarioScope",
    "ScenarioStore",
    "ServeState",
    "_int",
    "_scenario_path",
    "app_build_info",
    "app_scenarios_dir",
    "cancel_job",
    "crawl_command",
    "launchagent_plist",
    "list_apps",
    "list_fs",
    "list_runs",
    "list_scenarios",
    "list_simulators",
    "make_server",
    "mask_secret",
    "record_command",
    "run_command",
    "run_job",
    "scenario_out_path",
    "serve",
    "unique_scenario_path",
    "valid_backend",
    "valid_run_id",
    "valid_udid",
]


# The serve backends `_build_state` can assemble — the source of truth the CLI validates against.
# `local` runs in-process; `server` wires the hosted seams (Redis queue/log bus + object storage).
SERVE_BACKENDS: tuple[str, ...] = ("local", "server")


def _build_state(
    *,
    runs_dir: Path,
    config: Path | None,
    scenarios_dir: Path | None,
    root: Path | None,
    baselines_dir: Path | None,
    max_concurrent: int,
    token: str | None,
    backend: str = "local",
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
            )
        except ImportError as e:
            raise ImportError(
                "the server backend needs its optional extras — "
                "install with: pip install 'bajutsu[server,worker,db]'"
            ) from e
    return ServeState(
        runs_dir=runs_dir,
        config=config,
        scenarios_dir=scenarios_dir,
        root=root or Path.cwd(),
        baselines_dir=resolved_baselines,
        max_concurrent=max_concurrent,
        token=token,
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
) -> ServeState:
    """Wire the hosted seams from the environment (the single-tenant server backend, BE-0015).

    Redis (``BAJUTSU_REDIS_URL`` / ``BAJUTSU_QUEUE``) backs the run queue + log bus; one
    S3-compatible bucket (``BAJUTSU_S3_BUCKET`` / ``BAJUTSU_S3_ENDPOINT`` / ``BAJUTSU_S3_REGION``,
    optional ``BAJUTSU_S3_PREFIX`` tenant prefix) holds artifacts (``<prefix>artifacts/``) and
    scenarios (``<prefix>scenarios/<app>/``). Projects come from the bound config's apps — no
    Postgres registry in this path. Redis/RQ/boto3 are imported lazily, only here, so the default
    path and the import guard stay SDK-free."""
    import os

    from redis import Redis
    from rq import Queue

    from bajutsu.serve.helpers import list_apps
    from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
    from bajutsu.serve.server.baselines import ObjectBaselineStore
    from bajutsu.serve.server.db import repository_from_env
    from bajutsu.serve.server.executor import QueueExecutor
    from bajutsu.serve.server.logbus import RedisLogBus
    from bajutsu.serve.server.object_store import artifact_prefix, object_store_from_env, s3_prefix
    from bajutsu.serve.server.scenarios import ObjectScenarioStorage, StorageScenarioStore
    from bajutsu.serve.server.worker_job import redis_url

    store = object_store_from_env()
    if store is None:
        raise ValueError("BAJUTSU_S3_BUCKET is required for --backend=server")
    prefix = s3_prefix()
    # The real clients are wider than our minimal seam protocols (RedisLike / Queue), so hand them
    # over as Any — the seam adapters use only the slice they declare.
    redis: Any = Redis.from_url(redis_url())
    queue: Any = Queue(os.environ.get("BAJUTSU_QUEUE", "bajutsu"), connection=redis)

    state = ServeState(
        runs_dir=runs_dir,
        config=config,
        scenarios_dir=scenarios_dir,
        root=root,
        baselines_dir=baselines_dir,
        max_concurrent=max_concurrent,
        token=token,
        executor=QueueExecutor(queue),
        logbus=RedisLogBus(redis),
        # The system of record, when a database is configured (BAJUTSU_DATABASE_URL); None otherwise
        # so the server backend runs without one until 7b/7c need it (BE-0015 7a).
        repository=repository_from_env(),
    )
    # Override the filesystem seams (set local in __post_init__) with the object-storage ones. The
    # scenario store reads the live config's apps, so a config opened later is reflected.
    state.artifacts = ObjectStorageArtifactStore(store, prefix=artifact_prefix(prefix))
    state.scenarios = StorageScenarioStore(
        ObjectScenarioStorage(
            store, lambda: list_apps(state.config) if state.config else [], prefix=prefix
        )
    )
    state.baselines = ObjectBaselineStore(store, prefix=prefix)
    return state


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
    asgi: bool = False,
    backend: str = "local",
) -> None:
    state = _build_state(
        runs_dir=runs_dir,
        config=config,
        scenarios_dir=scenarios_dir,
        root=root,
        baselines_dir=baselines_dir,
        max_concurrent=max_concurrent,
        token=token,
        backend=backend,
    )
    hint = str(config) if config else "open a config.yml in the UI"
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

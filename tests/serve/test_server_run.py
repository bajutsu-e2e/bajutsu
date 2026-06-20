"""Tests for the serve seam-assembly factory + the uvicorn (ASGI) transport (BE-0015, D2).

`serve` separates two choices: the **transport** (the stdlib server, or the FastAPI app over
uvicorn — `--asgi`) and the **backend** (which seam implementations the ServeState carries —
`--backend`, only `local` until the hosted backings land). `_build_state` is the seam-assembly
factory both share; `make_asgi_server` runs the FastAPI app as a real ASGI server. These tests
exercise the factory and a real uvicorn server (beyond the TestClient coverage in
test_server_app.py), so the actual serving path — middleware, routing — is checked end to end.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from _shared import project

from bajutsu import serve as srv


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _state(tmp_path: Path) -> srv.ServeState:
    _scn, cfg, runs = project(tmp_path)
    return srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
    )


def test_build_state_assembles_local_seams(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert isinstance(state, srv.ServeState)
    assert isinstance(state.executor, srv.LocalExecutor)
    assert isinstance(state.logbus, srv.InMemoryLogBus)
    assert isinstance(state.artifacts, srv.LocalArtifactStore)
    assert isinstance(state.scenarios, srv.LocalScenarioStore)


def test_build_state_rejects_unknown_backend(tmp_path: Path) -> None:
    _scn, cfg, runs = project(tmp_path)
    # An unknown backend fails loudly rather than silently running local.
    with pytest.raises(ValueError, match="backend"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="bogus",
        )


def test_build_state_server_wires_the_hosted_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server backend assembles the hosted seams from the environment. The Redis/RQ/boto3
    # clients construct without connecting, so this checks the wiring (the seam types) on the gate.
    from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
    from bajutsu.serve.server.baselines import ObjectBaselineStore
    from bajutsu.serve.server.executor import QueueExecutor
    from bajutsu.serve.server.logbus import RedisLogBus
    from bajutsu.serve.server.scenarios import StorageScenarioStore

    monkeypatch.setenv("BAJUTSU_S3_BUCKET", "bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.executor, QueueExecutor)
    assert isinstance(state.logbus, RedisLogBus)
    assert isinstance(state.artifacts, ObjectStorageArtifactStore)
    assert isinstance(state.scenarios, StorageScenarioStore)
    assert isinstance(state.baselines, ObjectBaselineStore)
    # The scenario store reads the live config's apps (project registry comes from config here).
    scope = state.scenarios.scope("demo")
    assert scope is not None  # demo is an app in the bound config


def test_build_state_local_has_no_repository(tmp_path: Path) -> None:
    # The system of record is server-only; local never has one, so its behavior is unchanged.
    assert _state(tmp_path).repository is None


def test_build_state_server_has_no_repository_without_a_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The database is optional on the server backend: with BAJUTSU_DATABASE_URL unset the repository
    # stays None, so the existing server backing keeps working until a database is configured.
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    monkeypatch.setenv("BAJUTSU_S3_BUCKET", "bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.repository is None


def test_build_state_server_wires_the_repository_when_a_database_url_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With BAJUTSU_DATABASE_URL set, the server backend assembles a SqlRepository (SQLite here, so
    # it builds on the gate without a live Postgres).
    from bajutsu.serve.server.db import SqlRepository

    monkeypatch.setenv("BAJUTSU_S3_BUCKET", "bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.repository, SqlRepository)


def test_build_state_server_requires_a_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAJUTSU_S3_BUCKET", raising=False)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(ValueError, match="BAJUTSU_S3_BUCKET"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )


def test_build_state_server_without_extras_raises_a_clear_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With an extra missing (redis stood in here), the server assembly surfaces one install hint
    # naming the extras — not a raw ImportError for whichever module loaded first.
    import sys

    monkeypatch.setitem(sys.modules, "redis", None)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(srv.MissingServerExtra, match="extra"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )


def test_build_state_server_reraises_internal_import_bugs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed bajutsu.* import is a real bug, not a missing extra: it must surface unchanged rather
    # than be rewritten into the install hint (which would hide the traceback). Only third-party
    # extras get the hint.
    def boom(**_kwargs: object) -> srv.ServeState:
        raise ImportError("cannot import name 'X'", name="bajutsu.serve.server.db")

    monkeypatch.setattr(srv, "_build_server_state", boom)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(ImportError) as caught:
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )
    assert not isinstance(caught.value, srv.MissingServerExtra)
    assert "extra" not in str(caught.value)


def test_build_state_server_normalizes_a_prefix_without_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A prefix without a trailing slash must not fuse into "tenantartifacts/" / "tenantscenarios/".
    monkeypatch.setenv("BAJUTSU_S3_BUCKET", "bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_S3_PREFIX", "tenant")  # no trailing slash
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    # The artifact store keys a run-relative path under "<prefix>artifacts/", with the slash added.
    assert state.artifacts._key("r1/report.html") == "tenant/artifacts/r1/report.html"


def test_asgi_server_serves_the_app_over_a_real_socket(tmp_path: Path) -> None:
    state = _state(tmp_path)
    port = _free_port()
    server = srv.make_asgi_server(state, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started, "uvicorn server did not start"
        resp = httpx.get(f"http://127.0.0.1:{port}/api/runs", timeout=5)
        assert resp.status_code == 200 and resp.json() == []
        # The hardening middleware runs on the real server too (not just under TestClient).
        assert resp.headers["x-frame-options"] == "DENY"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "uvicorn server did not shut down"


def test_sse_route_delivers_keepalive_over_the_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # End to end over a real uvicorn server: an idle (running, not closed) job's /events stream
    # delivers the log frame and periodic `:keepalive` comments, so a proxy won't drop the idle
    # connection (B). Breaking out closes the socket — exercising the route's disconnect path.
    from bajutsu.serve.server import app as app_module

    monkeypatch.setattr(app_module, "_SSE_KEEPALIVE", 0.05)
    state = _state(tmp_path)
    state.jobs["w"] = srv.Job(id="w", cmd=[])
    state.logbus.publish(
        "w", "line one\n"
    )  # a line, but the job is NOT closed -> stream stays open
    port = _free_port()
    server = srv.make_asgi_server(state, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started
        seen_line = seen_keepalive = False
        with httpx.stream("GET", f"http://127.0.0.1:{port}/api/jobs/w/events", timeout=5) as r:
            for line in r.iter_lines():
                if "data: line one" in line:
                    seen_line = True
                if line.startswith(":keepalive"):
                    seen_keepalive = True
                if seen_line and seen_keepalive:
                    break
        assert seen_line and seen_keepalive
    finally:
        state.logbus.close("w")
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "uvicorn server did not shut down"

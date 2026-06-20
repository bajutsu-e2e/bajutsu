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
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


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
    # The hosted backend's seam backings (Redis / object store / DB) don't exist yet, so only the
    # local backend assembles; an unknown one fails loudly rather than silently running local.
    with pytest.raises(ValueError, match="backend"):
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

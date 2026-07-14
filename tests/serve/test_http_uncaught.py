"""Tests for the top-level uncaught-exception boundary (BE-0264, real server).

An operation that *raises* — rather than returning an ``({"error": …}, code)`` tuple — must
become a well-formed JSON 500, not an empty body that leaves the browser at
``Unexpected end of JSON input``. The streaming/binary routes own their own response lifecycle
and stay outside the boundary (a fallback ``_json`` would double-write their response)."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from _shared import _get, _post, _serve, project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops


def _boom(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("kaboom in the operation")


def test_post_operation_raise_becomes_json_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    monkeypatch.setattr(ops, "lint_scenario", _boom)
    try:
        code, body = _post(port, "/api/lint", {"text": "irrelevant"})
        assert code == 500
        assert "kaboom in the operation" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_get_operation_raise_becomes_json_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    monkeypatch.setattr(ops, "config_info", _boom)
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            _get(port, "/api/config")
        assert ei.value.code == 500
        assert "kaboom in the operation" in json.loads(ei.value.read())["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_streaming_route_stays_outside_the_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A streaming route owns its own response lifecycle, so it must NOT be wrapped: a raise there
    # propagates and drops the connection (the pre-BE-0264 behavior for these routes), rather than
    # being converted to a JSON 500 that would double-write an already-started response.
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    monkeypatch.setattr(ops, "job_log_events", _boom)
    try:
        with pytest.raises((urllib.error.URLError, http.client.RemoteDisconnected)):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/whatever/events", timeout=5)
    finally:
        server.shutdown()
        server.server_close()


def test_streaming_routes_still_serve_normally(tmp_path: Path) -> None:
    # Regression guard for the do_GET restructure: the streaming/binary routes still dispatch to
    # their own handlers (a missing run file is their own 404, not the generic match fallthrough).
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        with pytest.raises(urllib.error.HTTPError, match="404"):
            _get(port, "/runs/missing/screenshot.png")
        with pytest.raises(urllib.error.HTTPError, match="404"):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/nope/events")
    finally:
        server.shutdown()
        server.server_close()

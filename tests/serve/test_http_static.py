"""Tests for the bajutsu serve static assets, run listing, and simulators (real ThreadingHTTPServer)."""

from __future__ import annotations

import json
from pathlib import Path

from _shared import (
    _get,
    _get_json,
    _serve,
    project,
    write_run,
)

from bajutsu import serve as srv


def test_http_lists_and_index(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert b"bajutsu" in _get(port, "/")[1]
        assert _get_json(port, "/api/scenarios?app=demo")[0]["names"] == ["alpha", "beta"]
        assert _get_json(port, "/api/apps") == ["demo", "other"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_inlines_assets(tmp_path: Path) -> None:
    """The index serves one self-contained doc with the CSS/JS/themes inlined."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, body, ctype = _get(port, "/")
        text = body.decode("utf-8")
        assert status == 200 and ctype.startswith("text/html")
        assert '[data-theme="daylight"]' in text  # from serve.themes.css
        assert "--bg2" in text  # from serve.css (theme-aware inset color)
        assert "function showView" in text  # from serve.js
        assert "function applyTheme" in text  # the dark / light toggle logic
        assert "browseFs" in text  # config-browser JS survives the split
    finally:
        server.shutdown()
        server.server_close()


def test_serve_assets_present() -> None:
    """Guard against a template file going missing from the package."""
    for name in ("serve.html.j2", "serve.css", "serve.themes.css", "serve.js"):
        assert srv.handler._asset(name).strip()  # non-empty


def test_http_runs_history(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260610-1", ok=True, scenarios=[("alpha", True)])
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        hist = _get_json(port, "/api/runs")
        assert len(hist) == 1 and hist[0]["id"] == "20260610-1" and hist[0]["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_http_simulators(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                    {"udid": "U1", "name": "iPhone 17 Pro", "state": "Booted", "isAvailable": True}
                ]
            }
        }
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        simctl=lambda args, e=None: payload,
    )
    server, port = _serve(state)
    try:
        sims = _get_json(port, "/api/simulators")
        assert sims == [
            {"udid": "U1", "name": "iPhone 17 Pro", "runtime": "iOS 26.5", "booted": True}
        ]
    finally:
        server.shutdown()
        server.server_close()

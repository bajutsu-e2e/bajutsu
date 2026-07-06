"""Tests for the /api/coverage endpoint — the E2E coverage map via the web UI (BE-0146)."""

from __future__ import annotations

from pathlib import Path

from _shared import _post, _serve, project

from bajutsu import serve as srv


def test_coverage_returns_static_map_for_target(tmp_path: Path) -> None:
    """POST /api/coverage with a valid target returns the static map and a rendered report."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/coverage", {"target": "demo"})
        assert status == 200
        assert resp["target"] == "demo"
        assert set(resp["static"]) >= {"namespaces", "gaps", "off_namespace", "coverage"}
        assert "<html" in resp["html"].lower()
        # No run set was selected, so the run-evidence dimensions stay out of the payload.
        assert "endpoints" not in resp
    finally:
        server.shutdown()
        server.server_close()


def test_coverage_requires_target(tmp_path: Path) -> None:
    """POST /api/coverage without a target returns 400."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/coverage", {})
        assert status == 400
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_coverage_requires_config(tmp_path: Path) -> None:
    """POST /api/coverage without a bound config returns 400."""
    _scn_dir, _cfg, runs = project(tmp_path)
    state = srv.ServeState(config=None, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/coverage", {"target": "demo"})
        assert status == 400
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()

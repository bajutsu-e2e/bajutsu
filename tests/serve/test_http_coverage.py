"""Tests for the coverage endpoints — the E2E coverage map via the web UI (BE-0146).

Covers both surfaces over real HTTP: the view's `POST /api/coverage` and the linkable
`GET /coverage` page.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest
from _shared import _get, _post, _serve, project

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


def test_get_coverage_serves_a_linkable_html_page(tmp_path: Path) -> None:
    """GET /coverage?target=... renders the map as a page, the route /stats, /flakiness, and /usage
    each already have — so the Coverage view can be linked and opened directly (issue #1719)."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body, content_type = _get(port, "/coverage?target=demo")
        assert status == 200
        assert content_type.startswith("text/html")
        assert b"E2E coverage" in body and b"demo" in body
    finally:
        server.shutdown()
        server.server_close()


def test_get_coverage_without_a_target_explains_itself_as_html(tmp_path: Path) -> None:
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(port, "/coverage")
        assert excinfo.value.code == 400
        assert b"target is required" in excinfo.value.read()
        assert excinfo.value.headers.get("Content-Type", "").startswith("text/html")
    finally:
        server.shutdown()
        server.server_close()

"""Tests for the /api/audit endpoint — the static determinism audit in the web UI (BE-0145)."""

from __future__ import annotations

from pathlib import Path

from _shared import _post, _serve, project

from bajutsu import serve as srv


def test_audit_inline_yaml_returns_reports(tmp_path: Path) -> None:
    """POST /api/audit with inline yaml grades it without touching config or a device."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(
            port, "/api/audit", {"yaml": "- name: s\n  steps:\n    - tap: { id: home.ok }\n"}
        )
        assert status == 200
        assert resp["ok"] is True
        (report,) = resp["reports"]
        assert report["grade"] == "Stable"
        assert report["findings"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_audit_by_target_and_path(tmp_path: Path) -> None:
    """POST /api/audit with {target, path} reads the saved scenario and grades it (Replay view)."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/audit", {"target": "demo", "path": "smoke.yaml"})
        assert status == 200
        assert resp["ok"] is True
        assert {r["grade"] for r in resp["reports"]} == {"Stable"}
    finally:
        server.shutdown()
        server.server_close()


def test_audit_requires_input(tmp_path: Path) -> None:
    """POST /api/audit with neither yaml nor {target, path} returns 400."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/audit", {})
        assert status == 400
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_audit_unknown_path_returns_404(tmp_path: Path) -> None:
    """A {target, path} pointing at no scenario reads as not-found."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/audit", {"target": "demo", "path": "nope.yaml"})
        assert status == 404
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()

"""Tests for the bajutsu serve baseline approve endpoint (real ThreadingHTTPServer)."""

from __future__ import annotations

from pathlib import Path

from _shared import (
    _post_json,
    _serve,
    project,
)

from bajutsu import serve as srv


def test_http_approve_promotes_screenshot(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    baselines = tmp_path / "baselines"
    baselines.mkdir()
    sid_dir = runs / "20260610-1" / "00-home"
    sid_dir.mkdir(parents=True)
    (sid_dir / "visual-actual.png").write_bytes(b"PNGDATA")
    server, port = _serve(
        srv.ServeState(
            scenarios_dir=scn_dir, config=cfg, runs_dir=runs, baselines_dir=baselines, cwd=tmp_path
        )
    )
    try:
        code, body = _post_json(
            port, "/api/approve", {"runId": "20260610-1", "sid": "00-home", "baseline": "home.png"}
        )
        assert code == 200 and body["ok"] is True
        assert (baselines / "home.png").read_bytes() == b"PNGDATA"
    finally:
        server.shutdown()
        server.server_close()


def test_http_approve_rejects_traversal(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    baselines = tmp_path / "baselines"
    baselines.mkdir()
    sid_dir = runs / "r1" / "00-home"
    sid_dir.mkdir(parents=True)
    (sid_dir / "visual-actual.png").write_bytes(b"X")
    server, port = _serve(
        srv.ServeState(
            scenarios_dir=scn_dir, config=cfg, runs_dir=runs, baselines_dir=baselines, cwd=tmp_path
        )
    )
    try:
        code, body = _post_json(
            port, "/api/approve", {"runId": "r1", "sid": "00-home", "baseline": "../escape.png"}
        )
        assert code == 400 and "escape" in body["error"]
        assert not (tmp_path / "escape.png").exists()
    finally:
        server.shutdown()
        server.server_close()

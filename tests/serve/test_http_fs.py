"""Tests for the bajutsu serve filesystem browse and run-artifact serving (real ThreadingHTTPServer)."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest
from _shared import (
    _get,
    _get_json,
    _serve,
    project,
)

from bajutsu import serve as srv


def test_http_fs_lists_and_blocks_traversal(tmp_path: Path) -> None:
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        got = _get_json(port, "/api/fs")
        assert "bajutsu.config.yaml" in got["files"] and "scenarios" in got["dirs"]
        # A dir escaping the root is rejected (400), never listed.
        with pytest.raises(urllib.error.HTTPError, match="400"):
            _get(port, "/api/fs?dir=" + urllib.parse.quote(".."))
    finally:
        server.shutdown()
        server.server_close()


def test_http_serves_run_artifacts_and_blocks_traversal(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    (runs / "r1").mkdir()
    (runs / "r1" / "report.html").write_text("<html>hi</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, body, ctype = _get(port, "/runs/r1/report.html")
        assert status == 200 and b"hi" in body and "text/html" in ctype
        with pytest.raises(urllib.error.HTTPError, match="404"):
            _get(port, "/runs/../secret.txt")
    finally:
        server.shutdown()
        server.server_close()

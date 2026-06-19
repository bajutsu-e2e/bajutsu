"""Tests for the bajutsu serve crawl endpoint (real ThreadingHTTPServer)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from _shared import (
    FakeProc,
    _get,
    _get_json,
    _serve,
    project,
)

from bajutsu import serve as srv

# A screen map the fake crawl writes, mirroring what `screenmap_dict` produces.
SCREENMAP = {
    "nodes": [
        {"fingerprint": "aaa", "kind": "id", "ids": ["home.x"], "actions": ["home.x"]},
        {"fingerprint": "bbb", "kind": "id", "ids": ["next.y"], "actions": []},
    ],
    "edges": [{"src": "aaa", "action": "tap home.x", "dst": "bbb"}],
    "crashes": [],
}


def test_http_crawl_explores_and_streams_the_map(tmp_path: Path) -> None:
    """POST /api/crawl spawns the `crawl` command (the Crawl tab), returns the runId the screen
    map streams into, and that `runs/<id>/screenmap.json` is served back for the live graph."""
    scn_dir, cfg, runs = project(tmp_path)
    captured: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        captured.append(cmd)
        out = cmd[cmd.index("--out") + 1]  # the run dir the crawl streams its map into
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "screenmap.json").write_text(json.dumps(SCREENMAP), encoding="utf-8")
        return FakeProc(["crawl → " + out + "/screenmap.json\n", "crawled 2 screens\n"])

    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen)
    )
    try:
        body = json.dumps({"app": "demo", "maxScreens": 10, "maxSteps": 30}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/crawl",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req).read())
        assert resp["runId"]
        for _ in range(100):
            j = _get_json(port, "/api/jobs/" + resp["jobId"])
            if j["status"] == "done":
                break
            time.sleep(0.02)
        assert j["status"] == "done" and j["ok"] is True
        cmd = captured[0]
        assert cmd[1:5] == ["-m", "bajutsu", "crawl", "--app"]
        # The run dir passed to the CLI sits under the served runs dir, named by the returned id.
        assert cmd[cmd.index("--out") + 1] == str(runs / resp["runId"])
        assert cmd[cmd.index("--max-screens") + 1] == "10"
        # The streamed screen map is served back so the UI can draw the graph.
        status, raw, _ = _get(port, "/runs/" + resp["runId"] + "/screenmap.json")
        assert status == 200
        assert json.loads(raw) == SCREENMAP
    finally:
        server.shutdown()
        server.server_close()


def test_http_crawl_requires_app(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        body = json.dumps({"maxScreens": 5}).encode()  # missing app
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/crawl",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError, match="400"):
            urllib.request.urlopen(req)
    finally:
        server.shutdown()
        server.server_close()

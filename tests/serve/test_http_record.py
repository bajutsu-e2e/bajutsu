"""Tests for the bajutsu serve record endpoint (real ThreadingHTTPServer)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from _shared import (
    SCENARIO,
    FakeProc,
    _get_json,
    _post,
    _serve,
    project,
)

from bajutsu import serve as srv


def test_http_record_authors_scenario(tmp_path: Path) -> None:
    """POST /api/record spawns the `record` command (the Record tab), reports the out path, and
    on completion the authored `*.yaml` is readable via GET /api/scenario."""
    scn_dir, cfg, runs = project(tmp_path)
    captured: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        captured.append(cmd)
        out = cmd[cmd.index("--out") + 1]  # the OUT path the record command writes to
        (tmp_path / out).write_text(SCENARIO, encoding="utf-8")  # simulate the recorded scenario
        return FakeProc(["[1] -> tap #x\n", "recorded 1 steps (api agent) -> " + out + "\n"])

    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen)
    )
    try:
        body = json.dumps({"goal": "tap x", "app": "demo", "name": "authored"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/record",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req).read())
        assert resp["path"].endswith("authored.yaml")
        for _ in range(100):
            j = _get_json(port, "/api/jobs/" + resp["jobId"])
            if j["status"] == "done":
                break
            time.sleep(0.02)
        assert j["status"] == "done" and j["ok"] is True
        assert j["outPath"] == resp["path"]
        cmd = captured[0]
        assert cmd[1:6] == ["-m", "bajutsu", "record", "--out", str(scn_dir / "authored.yaml")]
        assert cmd[cmd.index("--goal") + 1] == "tap x"
        got = _get_json(port, "/api/scenario?app=demo&path=" + urllib.parse.quote(resp["path"]))
        assert got["yaml"] == SCENARIO  # the authored scenario is served back for the editor
    finally:
        server.shutdown()
        server.server_close()


def test_http_record_requires_goal_and_app(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        body = json.dumps({"goal": "tap x"}).encode()  # missing app
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/record",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError, match="400"):
            urllib.request.urlopen(req)
    finally:
        server.shutdown()
        server.server_close()


def test_http_record_rejects_unknown_backend(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post(
            port, "/api/record", {"goal": "tap x", "app": "demo", "backend": "rm -rf /"}
        )
        assert status == 400 and "unknown backend" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_record_rejects_invalid_udid(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post(
            port, "/api/record", {"goal": "tap x", "app": "demo", "udid": "A;rm -rf /"}
        )
        assert status == 400 and "invalid udid" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()

"""Tests for the bajutsu serve run/job endpoints (real ThreadingHTTPServer)."""

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
    _get_json,
    _post,
    _serve,
    fake_popen,
    project,
)

from bajutsu import serve as srv


def test_http_run_then_job_status(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["PASS  runs/done-1/manifest.json\n"]),
    )
    server, port = _serve(state)
    try:
        body = json.dumps({"scenario": "s.yaml", "app": "demo"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/run",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        job_id = json.loads(urllib.request.urlopen(req).read())["jobId"]
        for _ in range(100):
            j = _get_json(port, "/api/jobs/" + job_id)
            if j["status"] == "done":
                break
            time.sleep(0.02)
        assert j["status"] == "done" and j["ok"] is True and j["runId"] == "done-1"
    finally:
        server.shutdown()
        server.server_close()


def test_http_run_boots_pool_and_passes_workers(tmp_path: Path) -> None:
    """The UI's picked devices are booted, and the udid pool + workers reach the run command."""
    scn_dir, cfg, runs = project(tmp_path)
    captured: list[list[str]] = []
    boots: list[str] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        captured.append(cmd)
        return FakeProc(["PASS  runs/p/manifest.json\n"])

    def simctl(args: list[str], _e: object = None) -> str:
        boots.append(args[3])
        return ""

    server, port = _serve(
        srv.ServeState(
            scenarios_dir=scn_dir,
            config=cfg,
            runs_dir=runs,
            cwd=tmp_path,
            popen=popen,
            simctl=simctl,
        )
    )
    try:
        body = json.dumps(
            {"scenario": "s.yaml", "app": "demo", "udid": "A,B", "workers": 2}
        ).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/run",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req).read()
        for _ in range(100):  # the job runs on a background thread; wait for the spawn
            if captured:
                break
            time.sleep(0.02)
        cmd = captured[0]
        assert cmd[cmd.index("--udid") + 1] == "A,B"
        assert cmd[cmd.index("--workers") + 1] == "2"
        assert sorted(boots) == ["A", "B"]  # both picked devices booted before the run
    finally:
        server.shutdown()
        server.server_close()


def test_http_run_requires_scenario_and_app(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        body = json.dumps({"scenario": "s.yaml"}).encode()  # missing app
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/run",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError, match="400"):
            urllib.request.urlopen(req)
    finally:
        server.shutdown()
        server.server_close()


def test_http_run_requires_open_config(tmp_path: Path) -> None:
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))  # no config
    try:
        status, resp = _post(port, "/api/run", {"scenario": "s.yaml", "app": "demo"})
        assert status == 400 and "open a config" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()

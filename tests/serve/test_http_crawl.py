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
    _post,
    _serve,
    fake_popen,
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
        body = json.dumps(
            {"app": "demo", "agent": "claude-code", "maxScreens": 10, "maxSteps": 30}
        ).encode()
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
        assert cmd[cmd.index("--agent") + 1] == "claude-code"  # the Agent picked in the Crawl tab
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


def _crawl_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    scn_dir, cfg, runs = project(tmp_path)
    return _serve(
        srv.ServeState(
            scenarios_dir=scn_dir,
            config=cfg,
            runs_dir=runs,
            cwd=tmp_path,
            popen=fake_popen(["done\n"]),
        )
    )


def test_http_crawl_rejects_unknown_backend(tmp_path: Path) -> None:
    # A free-text backend must not reach the spawned `crawl` argv (BE-0051 slice 3 parity).
    server, port = _crawl_server(tmp_path)
    try:
        status, resp = _post(port, "/api/crawl", {"app": "demo", "backend": "rm -rf /"})
        assert status == 400 and "unknown backend" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_crawl_rejects_invalid_udid(tmp_path: Path) -> None:
    server, port = _crawl_server(tmp_path)
    try:
        status, resp = _post(port, "/api/crawl", {"app": "demo", "udid": "A;rm -rf /"})
        assert status == 400 and "invalid udid" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_crawl_rejected_at_concurrency_cap(tmp_path: Path) -> None:
    # Crawl is a long, device-heavy job, so it honours the same cap as run/record (BE-0051 slice 5).
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        max_concurrent=1,
        popen=fake_popen(["done\n"]),
    )
    state.jobs["seed"] = srv.Job(id="seed", cmd=[], status="running")  # already at the cap
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/crawl", {"app": "demo"})
        assert status == 429 and "too many concurrent jobs" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_valid_run_id_accepts_segments_rejects_paths() -> None:
    assert srv.valid_run_id("20260610-153045")  # the server-generated timestamp form
    assert srv.valid_run_id("run_1.2-3")
    for bad in ("/tmp/evil", "../escape", "a/b", "..", "", ".", "a\x00b"):
        assert not srv.valid_run_id(bad), bad


def test_http_crawl_resume_rejects_unsafe_run_id(tmp_path: Path) -> None:
    # Resuming takes runId from the client; it must not be able to redirect --out outside runs_dir.
    server, port = _crawl_server(tmp_path)
    try:
        for bad in ("/tmp/evil", "../../etc"):
            status, resp = _post(
                port,
                "/api/crawl",
                {"app": "demo", "resumeSrc": "a", "resumeKey": "k", "runId": bad},
            )
            assert status == 400 and "invalid runId" in resp["error"], bad
    finally:
        server.shutdown()
        server.server_close()

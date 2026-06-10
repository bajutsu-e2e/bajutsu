"""Tests for `bajutsu serve`: the pure helpers, the run-job logic (injected Popen), and the
HTTP endpoints (a real ThreadingHTTPServer on an ephemeral port)."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from bajutsu import serve as srv

CONFIG = "defaults: { backend: [idb] }\napps:\n  demo: { bundleId: com.example.demo }\n  other: { bundleId: com.example.other }\n"
SCENARIO = "- name: alpha\n  steps:\n    - tap: { id: home.title }\n- name: beta\n  steps:\n    - tap: { id: x }\n"


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    return scn_dir, cfg, runs


# --- pure helpers ---


def test_list_scenarios_parses_names(tmp_path: Path) -> None:
    scn_dir, _, _ = _project(tmp_path)
    got = srv.list_scenarios(scn_dir)
    assert len(got) == 1
    assert got[0]["file"] == "smoke.yaml"
    assert got[0]["names"] == ["alpha", "beta"]
    assert got[0]["path"].endswith("smoke.yaml")


def test_list_apps(tmp_path: Path) -> None:
    _, cfg, _ = _project(tmp_path)
    assert srv.list_apps(cfg) == ["demo", "other"]


def test_run_command_builder() -> None:
    cmd = srv.run_command("s.yaml", "demo", backend="idb", udid="U", config="c.yaml")
    assert cmd[:5] == [sys.executable, "-m", "bajutsu", "run", "s.yaml"]
    assert cmd[5:] == ["--app", "demo", "--config", "c.yaml", "--backend", "idb", "--udid", "U", "--no-erase"]
    erased = srv.run_command("s.yaml", "demo", erase=True, dismiss_alerts=True)
    assert "--erase" in erased and "--no-erase" not in erased and "--dismiss-alerts" in erased


# --- job logic with an injected Popen ---


class _FakeProc:
    def __init__(self, lines: list[str], code: int = 0) -> None:
        self.stdout: Iterator[str] = iter(lines)
        self.returncode = code

    def wait(self) -> None:
        pass


def _fake_popen(lines: list[str], code: int = 0):
    def popen(_cmd: list[str], **_kw: Any) -> _FakeProc:
        return _FakeProc(lines, code)

    return popen


def test_run_job_captures_output_and_run_id(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path,
                           popen=_fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]))
    job = state.new_job(["x"])
    srv.run_job(state, job)
    v = job.view()
    assert v["status"] == "done" and v["exitCode"] == 0 and v["ok"] is True
    assert v["runId"] == "20260610-1"
    assert "step 0 ok" in v["lines"]


def test_run_job_marks_failure(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path,
                           popen=_fake_popen(["FAIL  runs/r/manifest.json\n"], code=1))
    job = state.new_job(["x"])
    srv.run_job(state, job)
    assert job.view()["ok"] is False and job.view()["runId"] == "r"


# --- HTTP endpoints (real server) ---


def _serve(state: srv.ServeState):
    server = srv.make_server(state, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _get(port: int, path: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, r.read(), r.headers.get("Content-Type", "")


def _get_json(port: int, path: str) -> Any:
    return json.loads(_get(port, path)[1])


def test_http_lists_and_index(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path))
    try:
        assert b"bajutsu" in _get(port, "/")[1]
        assert _get_json(port, "/api/scenarios")[0]["names"] == ["alpha", "beta"]
        assert _get_json(port, "/api/apps") == ["demo", "other"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_run_then_job_status(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path,
                           popen=_fake_popen(["PASS  runs/done-1/manifest.json\n"]))
    server, port = _serve(state)
    try:
        body = json.dumps({"scenario": "s.yaml", "app": "demo"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=body,
                                     headers={"Content-Type": "application/json"})
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


def test_http_run_requires_scenario_and_app(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path))
    try:
        body = json.dumps({"scenario": "s.yaml"}).encode()  # missing app
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_http_serves_run_artifacts_and_blocks_traversal(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    (runs / "r1").mkdir()
    (runs / "r1" / "report.html").write_text("<html>hi</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path))
    try:
        status, body, ctype = _get(port, "/runs/r1/report.html")
        assert status == 200 and b"hi" in body and "text/html" in ctype
        try:
            _get(port, "/runs/../secret.txt")
            raise AssertionError("expected 404 for traversal")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
        server.server_close()

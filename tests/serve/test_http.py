"""Tests for `bajutsu serve`'s HTTP endpoints, against a real ThreadingHTTPServer."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from _shared import SCENARIO, FakeProc, fake_popen, project, write_run

from bajutsu import serve as srv


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
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert b"bajutsu" in _get(port, "/")[1]
        assert _get_json(port, "/api/scenarios?app=demo")[0]["names"] == ["alpha", "beta"]
        assert _get_json(port, "/api/apps") == ["demo", "other"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_inlines_assets(tmp_path: Path) -> None:
    """The index serves one self-contained doc with the CSS/JS/themes inlined."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, body, ctype = _get(port, "/")
        text = body.decode("utf-8")
        assert status == 200 and ctype.startswith("text/html")
        assert '[data-theme="daylight"]' in text  # from serve.themes.css
        assert "--bg2" in text  # from serve.css (theme-aware inset color)
        assert "function showView" in text  # from serve.js
        assert "function applyTheme" in text  # the dark / light toggle logic
        assert "browseFs" in text  # config-browser JS survives the split
    finally:
        server.shutdown()
        server.server_close()


def test_serve_assets_present() -> None:
    """Guard against a template file going missing from the package."""
    for name in ("serve.html.j2", "serve.css", "serve.themes.css", "serve.js"):
        assert srv.handler._asset(name).strip()  # non-empty


def test_http_runs_history(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260610-1", ok=True, scenarios=[("alpha", True)])
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        hist = _get_json(port, "/api/runs")
        assert len(hist) == 1 and hist[0]["id"] == "20260610-1" and hist[0]["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


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


def test_http_scenario_save_validates_and_writes(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        target = scn_dir / "smoke.yaml"
        edited = "- name: edited\n  steps:\n    - tap: { id: y }\n"
        body = json.dumps({"path": str(target), "yaml": edited}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenario",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req).read())["ok"] is True
        assert target.read_text(encoding="utf-8") == edited  # the edit landed on disk

        bad = json.dumps({"path": str(target), "yaml": "steps: [not, a, scenario, list"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenario",
            data=bad,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError, match="400"):
            urllib.request.urlopen(req)
        assert target.read_text(encoding="utf-8") == edited  # rejected save left the file intact
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


def test_http_simulators(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                    {"udid": "U1", "name": "iPhone 17 Pro", "state": "Booted", "isAvailable": True}
                ]
            }
        }
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        simctl=lambda args, e=None: payload,
    )
    server, port = _serve(state)
    try:
        sims = _get_json(port, "/api/simulators")
        assert sims == [
            {"udid": "U1", "name": "iPhone 17 Pro", "runtime": "iOS 26.5", "booted": True}
        ]
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


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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


def test_http_open_config_binds_and_lists_apps(tmp_path: Path) -> None:
    _, _, runs = project(tmp_path)
    # No config bound at startup; the browse root is the project dir.
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/config")["hasConfig"] is False
        assert _get_json(port, "/api/apps") == []  # nothing until a config is opened
        status, resp = _post(port, "/api/config", {"path": "bajutsu.config.yaml"})
        assert status == 200 and resp["ok"] is True and resp["apps"] == ["demo", "other"]
        assert _get_json(port, "/api/config")["hasConfig"] is True
        assert _get_json(port, "/api/apps") == ["demo", "other"]
        # A path outside the browse root is rejected.
        status, _ = _post(port, "/api/config", {"path": "/etc/hosts"})
        assert status == 400
        # A path inside root but not a config file → 404.
        status, _ = _post(port, "/api/config", {"path": "nope.yaml"})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenarios_by_app_from_config(tmp_path: Path) -> None:
    _, cfg, runs = project(tmp_path)
    # Config-driven (no --scenarios override): the dir comes from the selected app.
    server, port = _serve(srv.ServeState(runs_dir=runs, config=cfg, root=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/scenarios?app=demo")[0]["names"] == ["alpha", "beta"]
        assert _get_json(port, "/api/scenarios?app=other") == []  # app has no scenarios dir
        assert _get_json(port, "/api/scenarios") == []  # no app → nothing
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


def _post_json(port: int, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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


def test_http_api_key_set_reveal_and_clear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-trip the Claude API key through the WebUI: unset → set (redacted) → reveal → clear.
    The key is held in the serve process's environment only (in memory) — never written to disk —
    so a spawned job inherits it via os.environ."""
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # clean start + auto-restore at teardown
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert _get_json(port, "/api/apikey") == {"set": False}
        # Set it: the response redacts the value, and it lands in the process env (not on disk).
        code, body = _post(port, "/api/apikey", {"value": "sk-ant-secret-12345"})
        assert code == 200 and body["set"] is True
        assert body["masked"] == "sk-a…2345" and "secret" not in body["masked"]
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-12345"
        assert not (tmp_path / ".env").exists()  # nothing is persisted to disk
        # GET is redacted by default; ?reveal=1 returns the full value.
        assert _get_json(port, "/api/apikey") == {"set": True, "masked": "sk-a…2345"}
        assert _get_json(port, "/api/apikey?reveal=1")["value"] == "sk-ant-secret-12345"
        # An empty value clears it.
        code, body = _post(port, "/api/apikey", {"value": ""})
        assert code == 200 and body["set"] is False
        assert _get_json(port, "/api/apikey") == {"set": False}
        assert "ANTHROPIC_API_KEY" not in os.environ
    finally:
        server.shutdown()
        server.server_close()


def test_http_api_key_rejects_whitespace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/apikey", {"value": "sk ant with spaces"})
        assert code == 400 and "whitespace" in body["error"]
        assert _get_json(port, "/api/apikey") == {"set": False}
    finally:
        server.shutdown()
        server.server_close()

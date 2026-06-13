"""Tests for `bajutsu serve`: the pure helpers, the run-job logic (injected Popen), and the
HTTP endpoints (a real ThreadingHTTPServer on an ephemeral port)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

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


def test_list_scenarios_includes_descriptions(tmp_path: Path) -> None:
    d = tmp_path / "scn"
    d.mkdir()
    (d / "described.yaml").write_text(
        "description: file note\nscenarios:\n  - name: a\n    description: scn note\n"
        "    steps:\n      - tap: { id: x }\n",
        encoding="utf-8",
    )
    got = srv.list_scenarios(d)
    assert got[0]["description"] == "file note"
    assert got[0]["scenarios"] == [{"name": "a", "description": "scn note"}]
    assert got[0]["names"] == ["a"]


def _write_run(runs: Path, run_id: str, *, ok: bool, scenarios: list[tuple[str, bool]]) -> None:
    d = runs / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "scenarios": [{"scenario": n, "ok": o} for n, o in scenarios],
            }
        ),
        encoding="utf-8",
    )
    (d / "report.html").write_text("<html></html>", encoding="utf-8")


def test_list_runs_newest_first_with_summary(tmp_path: Path) -> None:
    _, _, runs = _project(tmp_path)
    _write_run(runs, "20260610-1", ok=True, scenarios=[("alpha", True)])
    _write_run(runs, "20260610-2", ok=False, scenarios=[("alpha", True), ("beta", False)])
    (runs / "not-a-run").mkdir()  # no manifest → skipped
    got = srv.list_runs(runs)
    assert [r["id"] for r in got] == ["20260610-2", "20260610-1"]  # newest first
    assert got[0]["ok"] is False and got[0]["passed"] == 1 and got[0]["total"] == 2
    assert got[0]["scenarios"] == ["alpha", "beta"] and got[0]["report"] is True
    assert got[1]["ok"] is True


def test_list_runs_empty_dir(tmp_path: Path) -> None:
    assert srv.list_runs(tmp_path / "nope") == []


def test_run_command_builder() -> None:
    cmd = srv.run_command("s.yaml", "demo", backend="idb", udid="U", config="c.yaml")
    assert cmd[:5] == [sys.executable, "-m", "bajutsu", "run", "s.yaml"]
    # erase defaults to None: no flag, so each scenario's preconditions.erase decides.
    # --progress is always passed so the run streams scenario/step lines into the run log.
    assert cmd[5:] == [
        "--app",
        "demo",
        "--config",
        "c.yaml",
        "--progress",
        "--backend",
        "idb",
        "--udid",
        "U",
    ]
    assert "--erase" not in cmd and "--no-erase" not in cmd
    erased = srv.run_command("s.yaml", "demo", erase=True, dismiss_alerts=True)
    assert "--erase" in erased and "--no-erase" not in erased and "--dismiss-alerts" in erased
    assert "--no-erase" in srv.run_command("s.yaml", "demo", erase=False)  # explicit override
    # dismiss_alerts defaults to None: no flag, so each scenario's dismissAlerts (on) decides.
    assert "--dismiss-alerts" not in cmd and "--no-dismiss-alerts" not in cmd
    # False forces the guard off for the run (mirrors --no-erase).
    assert "--no-dismiss-alerts" in srv.run_command("s.yaml", "demo", dismiss_alerts=False)


def test_run_command_parallel_pool() -> None:
    cmd = srv.run_command("s.yaml", "demo", udid="A,B", workers=2)
    assert cmd[cmd.index("--udid") + 1] == "A,B"  # comma list passes through as a device pool
    assert cmd[cmd.index("--workers") + 1] == "2"
    assert "--workers" not in srv.run_command("s.yaml", "demo", workers=1)  # single-device omits it


def test_record_command_builder() -> None:
    cmd = srv.record_command(
        "out.yaml",
        "demo",
        "tap Increment",
        agent="claude-code",
        backend="idb",
        udid="U",
        config="c.yaml",
    )
    assert cmd[:5] == [sys.executable, "-m", "bajutsu", "record", "out.yaml"]
    assert cmd[5:11] == ["--app", "demo", "--goal", "tap Increment", "--config", "c.yaml"]
    assert cmd[cmd.index("--agent") + 1] == "claude-code"
    assert cmd[cmd.index("--backend") + 1] == "idb" and cmd[cmd.index("--udid") + 1] == "U"
    # erase / dismiss default to None (the CLI defaults — record erases and dismisses): no flag.
    assert "--erase" not in cmd and "--no-erase" not in cmd and "--no-dismiss-alerts" not in cmd
    # Explicit overrides mirror run_command.
    assert "--no-erase" in srv.record_command("o.yaml", "demo", "g", erase=False)
    assert "--no-dismiss-alerts" in srv.record_command("o.yaml", "demo", "g", dismiss_alerts=False)
    bare = srv.record_command("o.yaml", "demo", "g")  # no agent → no --agent (CLI default applies)
    assert "--agent" not in bare and "--backend" not in bare


def test_scenario_out_path_sanitizes(tmp_path: Path) -> None:
    d = tmp_path / "scn"
    assert srv.scenario_out_path(d, "login") == d / "login.yaml"
    assert srv.scenario_out_path(d, "login.yaml") == d / "login.yaml"  # suffix normalized
    assert srv.scenario_out_path(d, "a/b/../c") == d / "a-b-..-c.yaml"  # no escape via separators
    assert srv.scenario_out_path(d, "") == d / "authored.yaml"  # blank → fallback
    assert srv.scenario_out_path(d, "   ") == d / "authored.yaml"
    assert srv.scenario_out_path(d, "..") == d / "authored.yaml"  # never names the parent dir


def test_unique_scenario_path_stamps_existing(tmp_path: Path) -> None:
    p = tmp_path / "generated.yaml"
    assert srv.unique_scenario_path(p) == p  # free → unchanged
    p.write_text("a", encoding="utf-8")
    # taken → the run's date-time is appended so nothing is overwritten
    assert (
        srv.unique_scenario_path(p, stamp="20260613-153045")
        == tmp_path / "generated-20260613-153045.yaml"
    )


def test_scenario_path_guard_keeps_inside_dir(tmp_path: Path) -> None:
    d = tmp_path / "scn"
    d.mkdir()
    assert srv._scenario_path(d, None) is None
    assert srv._scenario_path(d, "smoke.yaml") == (d / "smoke.yaml").resolve()
    assert srv._scenario_path(d, str(d / "x.yaml")) == (d / "x.yaml").resolve()
    assert srv._scenario_path(d, "x.txt") is None  # only *.yaml
    assert srv._scenario_path(d, "../escape.yaml") is None  # traversal blocked
    assert srv._scenario_path(d, str(tmp_path / "outside.yaml")) is None


def test_int_coercion() -> None:
    assert srv._int(3, 1) == 3 and srv._int("4", 1) == 4
    assert srv._int(None, 1) == 1 and srv._int("x", 1) == 1  # bad values fall back


def _boom(_args: list[str], _e: object = None) -> str:
    raise OSError("simctl not found")


def test_list_simulators_parses_and_orders() -> None:
    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                    {"udid": "B1", "name": "iPhone 17", "state": "Shutdown", "isAvailable": True},
                    {"udid": "A1", "name": "iPhone 17 Pro", "state": "Booted", "isAvailable": True},
                    {
                        "udid": "X",
                        "name": "old",
                        "state": "Shutdown",
                        "isAvailable": False,
                    },  # filtered out
                ],
            }
        }
    )
    sims = srv.list_simulators(simctl=lambda args, e=None: payload)
    assert [s["udid"] for s in sims] == ["A1", "B1"]  # booted first, then by name
    assert sims[0] == {"udid": "A1", "name": "iPhone 17 Pro", "runtime": "iOS 26.5", "booted": True}
    assert srv.list_simulators(simctl=_boom) == []  # failure -> empty, never raises


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
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=_fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
    )
    job = state.new_job(["x"])
    srv.run_job(state, job)
    v = job.view()
    assert v["status"] == "done" and v["exitCode"] == 0 and v["ok"] is True
    assert v["runId"] == "20260610-1"
    assert "step 0 ok" in v["lines"]


def test_run_job_marks_failure(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=_fake_popen(["FAIL  runs/r/manifest.json\n"], code=1),
    )
    job = state.new_job(["x"])
    srv.run_job(state, job)
    assert job.view()["ok"] is False and job.view()["runId"] == "r"


def test_run_job_builds_app_when_binary_missing(tmp_path: Path) -> None:
    """A missing binary triggers the app's `build` command before the run; both spawns share the
    injected Popen, so we record each command and synthesize the binary during the build."""
    scn_dir, cfg, runs = _project(tmp_path)
    app_path = "MyApp.app"
    calls: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> _FakeProc:
        calls.append(cmd)
        if cmd == "make build":  # the build command — create the binary it produces
            (tmp_path / app_path).mkdir()
            return _FakeProc(["compiling…\n"])
        return _FakeProc(["PASS  runs/r/manifest.json\n"])  # the run

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.new_job(["run"], app_path=app_path, build="make build")
    srv.run_job(state, job)
    v = job.view()
    assert calls == ["make build", ["run"]]  # build first, then the run
    assert v["ok"] is True and v["runId"] == "r"
    assert any("building: make build" in line for line in v["lines"])
    assert any("build ok" in line for line in v["lines"])


def test_run_job_skips_build_when_binary_exists(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    (tmp_path / "MyApp.app").mkdir()  # binary already present → no build
    calls: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> _FakeProc:
        calls.append(cmd)
        return _FakeProc(["PASS  runs/r/manifest.json\n"])

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.new_job(["run"], app_path="MyApp.app", build="make build")
    srv.run_job(state, job)
    assert calls == [["run"]]  # only the run spawned; the build was skipped
    assert job.view()["ok"] is True


def test_run_job_build_failure_skips_the_run(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    spawned: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> _FakeProc:
        spawned.append(cmd)
        return _FakeProc(["build error\n"], code=2)  # build never creates the binary

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.new_job(["run"], app_path="MyApp.app", build="make build")
    srv.run_job(state, job)
    v = job.view()
    assert spawned == ["make build"]  # the run is not spawned when the build fails
    assert v["status"] == "done" and v["ok"] is False and v["exitCode"] == 2
    assert any("build failed" in line for line in v["lines"])


def test_app_build_info_reads_config(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "apps:\n  demo:\n    bundleId: com.example.demo\n"
        "    appPath: build/Demo.app\n    build: make demo\n  bare: { bundleId: com.example.bare }\n",
        encoding="utf-8",
    )
    assert srv.app_build_info(cfg, "demo") == ("build/Demo.app", "make demo")
    assert srv.app_build_info(cfg, "bare") == (None, None)  # neither set
    assert srv.app_build_info(cfg, "nope") == (None, None)  # unknown app → no build
    assert srv.app_build_info(tmp_path / "missing.yaml", "demo") == (None, None)  # no config


def test_run_job_boots_devices_before_running(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    boots: list[str] = []

    def simctl(args: list[str], _e: object = None) -> str:
        boots.append(args[3])  # ["xcrun","simctl","bootstatus",<udid>,"-b"]
        return ""

    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=_fake_popen(["PASS  runs/r/manifest.json\n"]),
        simctl=simctl,
    )
    job = state.new_job(["x"], udids=["U1", "U2"])
    srv.run_job(state, job)
    assert sorted(boots) == ["U1", "U2"]  # every picked device booted before the run (parallel)
    v = job.view()
    assert v["ok"] is True and v["runId"] == "r"
    assert any("booting U1" in line for line in v["lines"])


def test_run_job_boot_failure_skips_the_run(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    spawned: list[Any] = []

    def simctl(args: list[str], _e: object = None) -> str:
        raise subprocess.CalledProcessError(1, args, stderr="Invalid device")

    def popen(*a: Any, **_k: Any) -> _FakeProc:
        spawned.append(a)
        return _FakeProc([])

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen, simctl=simctl
    )
    job = state.new_job(["x"], udids=["BAD"])
    srv.run_job(state, job)
    v = job.view()
    assert v["status"] == "done" and v["ok"] is False
    assert spawned == []  # the run is not spawned when a device won't boot
    assert any("boot failed" in line for line in v["lines"])


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
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert b"bajutsu" in _get(port, "/")[1]
        assert _get_json(port, "/api/scenarios")[0]["names"] == ["alpha", "beta"]
        assert _get_json(port, "/api/apps") == ["demo", "other"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_runs_history(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    _write_run(runs, "20260610-1", ok=True, scenarios=[("alpha", True)])
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
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=_fake_popen(["PASS  runs/done-1/manifest.json\n"]),
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
    scn_dir, cfg, runs = _project(tmp_path)
    captured: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> _FakeProc:
        captured.append(cmd)
        out = cmd[cmd.index("record") + 1]  # the OUT path the record command writes to
        (tmp_path / out).write_text(SCENARIO, encoding="utf-8")  # simulate the recorded scenario
        return _FakeProc(["[1] -> tap #x\n", "recorded 1 steps (api agent) -> " + out + "\n"])

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
        assert cmd[1:5] == ["-m", "bajutsu", "record", str(scn_dir / "authored.yaml")]
        assert cmd[cmd.index("--goal") + 1] == "tap x"
        got = _get_json(port, "/api/scenario?path=" + urllib.parse.quote(resp["path"]))
        assert got["yaml"] == SCENARIO  # the authored scenario is served back for the editor
    finally:
        server.shutdown()
        server.server_close()


def test_http_record_requires_goal_and_app(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
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
    scn_dir, cfg, runs = _project(tmp_path)
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
    scn_dir, cfg, runs = _project(tmp_path)
    captured: list[list[str]] = []
    boots: list[str] = []

    def popen(cmd: list[str], **_kw: Any) -> _FakeProc:
        captured.append(cmd)
        return _FakeProc(["PASS  runs/p/manifest.json\n"])

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
    scn_dir, cfg, runs = _project(tmp_path)
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
    scn_dir, cfg, runs = _project(tmp_path)
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


def test_http_serves_run_artifacts_and_blocks_traversal(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
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

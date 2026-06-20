"""Tests for `bajutsu serve`'s run-job logic, driving it with an injected Popen."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from _shared import FakeProc, fake_popen, project

from bajutsu import serve as srv


def test_run_job_captures_output_and_run_id(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
    )
    job = state.new_job(["x"])
    srv.run_job(state, job)
    v = job.view()
    assert v["status"] == "done" and v["exitCode"] == 0 and v["ok"] is True
    assert v["runId"] == "20260610-1"
    assert "step 0 ok" in v["lines"]


def test_run_job_marks_failure(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["FAIL  runs/r/manifest.json\n"], code=1),
    )
    job = state.new_job(["x"])
    srv.run_job(state, job)
    assert job.view()["ok"] is False and job.view()["runId"] == "r"


def test_run_job_builds_app_when_binary_missing(tmp_path: Path) -> None:
    """A missing binary triggers the app's `build` command before the run; both spawns share the
    injected Popen, so we record each command and synthesize the binary during the build."""
    scn_dir, cfg, runs = project(tmp_path)
    app_path = "MyApp.app"
    calls: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> FakeProc:
        calls.append(cmd)
        if cmd == ["make", "build"]:  # the build command — create the binary it produces
            (tmp_path / app_path).mkdir()
            return FakeProc(["compiling…\n"])
        return FakeProc(["PASS  runs/r/manifest.json\n"])  # the run

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.new_job(["run"], app_path=app_path, build="make build")
    srv.run_job(state, job)
    v = job.view()
    assert calls == [["make", "build"], ["run"]]  # build first (shlex-split), then the run
    assert v["ok"] is True and v["runId"] == "r"
    assert any("building: make build" in line for line in v["lines"])
    assert any("build ok" in line for line in v["lines"])


def test_run_job_skips_build_when_binary_exists(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    (tmp_path / "MyApp.app").mkdir()  # binary already present → no build
    calls: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> FakeProc:
        calls.append(cmd)
        return FakeProc(["PASS  runs/r/manifest.json\n"])

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.new_job(["run"], app_path="MyApp.app", build="make build")
    srv.run_job(state, job)
    assert calls == [["run"]]  # only the run spawned; the build was skipped
    assert job.view()["ok"] is True


def test_run_job_build_failure_skips_the_run(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    spawned: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> FakeProc:
        spawned.append(cmd)
        return FakeProc(["build error\n"], code=2)  # build never creates the binary

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.new_job(["run"], app_path="MyApp.app", build="make build")
    srv.run_job(state, job)
    v = job.view()
    assert spawned == [["make", "build"]]  # the run is not spawned when the build fails
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


def test_app_scenarios_dir_reads_config(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "apps:\n  demo: { bundleId: com.example.demo, scenarios: scn/dir }\n"
        "  bare: { bundleId: com.example.bare }\n",
        encoding="utf-8",
    )
    assert srv.app_scenarios_dir(cfg, "demo") == Path("scn/dir")
    assert srv.app_scenarios_dir(cfg, "bare") is None  # unset
    assert srv.app_scenarios_dir(cfg, "nope") is None  # unknown app
    assert srv.app_scenarios_dir(tmp_path / "missing.yaml", "demo") is None  # no config


def test_list_fs_lists_dirs_and_yaml(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.yaml").write_text("- name: a\n  steps: []\n", encoding="utf-8")
    (tmp_path / "b.yml").write_text("x", encoding="utf-8")
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")  # non-yaml, excluded
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")  # hidden, excluded
    got = srv.list_fs(tmp_path, None)
    assert got["cwd"] == str(tmp_path.resolve())
    assert got["parent"] is None  # at the browse ceiling
    assert got["dirs"] == ["sub"]
    assert got["files"] == ["a.yaml", "b.yml"]
    # Descending into a subdir exposes a parent to climb back to (but not above root).
    deeper = srv.list_fs(tmp_path, "sub")
    assert deeper["parent"] == str(tmp_path.resolve())


def test_list_fs_blocks_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="outside root"):
        srv.list_fs(root, "..")
    with pytest.raises(ValueError, match="outside root"):
        srv.list_fs(root, str(tmp_path))  # absolute, outside root


def test_run_job_boots_devices_before_running(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    boots: list[str] = []

    def simctl(args: list[str], _e: object = None) -> str:
        boots.append(args[3])  # ["xcrun","simctl","bootstatus",<udid>,"-b"]
        return ""

    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["PASS  runs/r/manifest.json\n"]),
        simctl=simctl,
    )
    job = state.new_job(["x"], udids=["U1", "U2"])
    srv.run_job(state, job)
    assert sorted(boots) == ["U1", "U2"]  # every picked device booted before the run (parallel)
    v = job.view()
    assert v["ok"] is True and v["runId"] == "r"
    assert any("booting U1" in line for line in v["lines"])


def test_run_job_boot_failure_skips_the_run(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    spawned: list[Any] = []

    def simctl(args: list[str], _e: object = None) -> str:
        raise subprocess.CalledProcessError(1, args, stderr="Invalid device")

    def popen(*a: Any, **_k: Any) -> FakeProc:
        spawned.append(a)
        return FakeProc([])

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen, simctl=simctl
    )
    job = state.new_job(["x"], udids=["BAD"])
    srv.run_job(state, job)
    v = job.view()
    assert v["status"] == "done" and v["ok"] is False
    assert spawned == []  # the run is not spawned when a device won't boot
    assert any("boot failed" in line for line in v["lines"])


def test_run_job_terminates_process_on_output_error(tmp_path: Path) -> None:
    """If stdout iteration raises, the process must be terminated so it doesn't leak."""
    scn_dir, cfg, runs = project(tmp_path)
    terminated: list[bool] = []

    class _ExplodingProc:
        def __init__(self) -> None:
            self.returncode = 1

        @property
        def stdout(self):
            def _boom():
                yield "line 1\n"
                raise OSError("broken pipe")

            return _boom()

        def wait(self) -> None:
            pass

        def terminate(self) -> None:
            terminated.append(True)

    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=lambda *_a, **_kw: _ExplodingProc(),
    )
    job = state.new_job(["run"])
    srv.run_job(state, job)
    assert terminated, "process was not terminated after stdout error"
    assert job.view()["status"] == "done"


def test_build_app_terminates_process_on_output_error(tmp_path: Path) -> None:
    """If stdout iteration raises during build, the build process must be terminated."""
    scn_dir, cfg, runs = project(tmp_path)
    terminated: list[bool] = []

    class _ExplodingProc:
        def __init__(self) -> None:
            self.returncode = 1

        @property
        def stdout(self):
            def _boom():
                yield "compiling…\n"
                raise OSError("broken pipe")

            return _boom()

        def wait(self) -> None:
            pass

        def terminate(self) -> None:
            terminated.append(True)

    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=lambda *_a, **_kw: _ExplodingProc(),
    )
    job = state.new_job(["run"], app_path="Missing.app", build="make build")
    srv.run_job(state, job)
    assert terminated, "build process was not terminated after stdout error"
    assert job.view()["status"] == "done"


def test_try_new_job_caps_concurrency_per_user(tmp_path: Path) -> None:
    # A per-user cap stops one user from monopolizing the scarce device pool (BE-0015 7c-3).
    state = srv.ServeState(runs_dir=tmp_path / "runs", max_concurrent_per_user=1)
    assert state.try_new_job([], actor="alice") is not None  # alice's first job
    assert state.try_new_job([], actor="alice") is None  # alice is at her cap
    assert state.try_new_job([], actor="bob") is not None  # a different user is unaffected


def test_try_new_job_per_user_cap_ignores_anonymous_jobs(tmp_path: Path) -> None:
    # Token/anonymous jobs (no identity) aren't subject to the per-user cap — only the global one.
    state = srv.ServeState(runs_dir=tmp_path / "runs", max_concurrent_per_user=1)
    assert state.try_new_job([], actor=None) is not None
    assert state.try_new_job([], actor=None) is not None


def test_try_new_job_per_user_unlimited_by_default(tmp_path: Path) -> None:
    # Default 0 = unlimited, so behavior is unchanged unless an operator opts in.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    assert state.try_new_job([], actor="alice") is not None
    assert state.try_new_job([], actor="alice") is not None

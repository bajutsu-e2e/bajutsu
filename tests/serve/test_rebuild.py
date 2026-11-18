"""Tests for serve's app build on every native platform, and the `rebuild` flag of `POST /api/run`.

serve builds the app itself before it spawns the run: a missing binary on demand, or always when
the request sets `rebuild`. These cases cover the request gate, the job's build phase, and the
job spec that carries the flag to a remote worker.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from _shared import FakeProc, project
from conftest import FakeObjectStore

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.worker_job import job_spec
from bajutsu.serve.upload_artifacts import artifact_store_key

_CONFIG = (
    "targets:\n"
    "  demo:\n"
    "    platform: android\n"
    "    subject: {id: com.example.demo, path: ./build/demo.apk, build: make apk}\n"
    "    paths: {scenarios: ./scenarios}\n"
    "  bare:\n"
    "    platform: android\n"
    "    subject: {id: com.example.bare, path: ./build/bare.apk}\n"
    "    paths: {scenarios: ./scenarios}\n"
)


class _Recorder:
    """A non-local executor: records what was dispatched instead of running it."""

    def __init__(self) -> None:
        self.jobs: list[srv.Job] = []

    def dispatch(self, state: srv.ServeState, job: srv.Job) -> None:
        self.jobs.append(job)


def _state(tmp_path: Path) -> tuple[srv.ServeState, _Recorder]:
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "s.yaml").write_text("- name: a\n  steps: []\n", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(_CONFIG, encoding="utf-8")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, cwd=tmp_path)
    recorder = _Recorder()
    state.executor = recorder
    return state, recorder


def _run(state: srv.ServeState, **body: Any) -> tuple[Any, int]:
    return ops.start_run(state, {"scenario": "s.yaml", "target": "demo", **body})


def test_an_android_target_hands_its_build_to_the_job(tmp_path: Path) -> None:
    state, recorder = _state(tmp_path)
    _resp, code = _run(state)
    assert code == 200
    job = recorder.jobs[-1]
    assert (job.app_path, job.build, job.rebuild) == ("./build/demo.apk", "make apk", False)


def test_rebuild_is_set_on_the_job_and_carried_to_a_worker(tmp_path: Path) -> None:
    state, recorder = _state(tmp_path)
    _resp, code = _run(state, rebuild=True)
    assert code == 200
    job = recorder.jobs[-1]
    assert job.rebuild is True
    assert job_spec(job)["rebuild"] is True
    # serve builds, so the spawned run itself is never told to rebuild.
    assert "--rebuild" not in job.cmd


def test_rebuild_on_a_target_without_a_build_is_refused(tmp_path: Path) -> None:
    state, recorder = _state(tmp_path)
    resp, code = _run(state, target="bare", rebuild=True)
    assert code == 400
    assert "subject.build" in resp["error"]
    assert not recorder.jobs


def test_rebuild_on_a_withheld_build_is_refused(tmp_path: Path) -> None:
    # An API-bound Git config without --allow-remote-build has its build nulled, so a forced build
    # has nothing serve may run.
    state, recorder = _state(tmp_path)
    state.binding = replace(state.binding, git_from_api=True)
    _resp, code = _run(state, rebuild=True)
    assert code == 400
    assert not recorder.jobs


def test_rebuild_with_a_binary_artifact_is_refused(tmp_path: Path) -> None:
    state, recorder = _state(tmp_path)
    state.repository = MagicMock()  # overrides need the job queue
    store = FakeObjectStore()
    state.object_store = store
    sha = "a" * 64
    store.objects[artifact_store_key("", "default", "binary", sha)] = b"apk"
    resp, code = _run(state, rebuild=True, binaryArtifact=sha)
    assert code == 400
    assert "binaryArtifact" in resp["error"]
    assert not recorder.jobs


def test_a_non_boolean_rebuild_is_refused(tmp_path: Path) -> None:
    # Read as off, `"true"` would run the existing binary the caller meant to replace.
    state, recorder = _state(tmp_path)
    resp, code = _run(state, rebuild="true")
    assert code == 400
    assert resp["error"] == "rebuild must be a boolean"
    assert not recorder.jobs


def test_rebuild_false_runs_as_before(tmp_path: Path) -> None:
    state, recorder = _state(tmp_path)
    _resp, code = _run(state, rebuild=False)
    assert code == 200
    assert recorder.jobs[-1].rebuild is False


def test_a_rebuild_job_builds_even_when_the_binary_exists(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    (tmp_path / "MyApp.apk").touch()
    calls: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> FakeProc:
        calls.append(cmd)
        return FakeProc(["PASS  runs/r/manifest.json\n"])

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.register(srv.Job(cmd=["run"], app_path="MyApp.apk", build="make apk", rebuild=True))
    srv.run_job(state, job)
    assert calls == [["make", "apk"], ["run"]]  # the build ran despite the existing binary
    assert any("rebuilding the app: make apk" in line for line in job.view()["lines"])


def test_a_rebuild_routes_to_a_worker_that_builds_the_platform(tmp_path: Path) -> None:
    state, recorder = _state(tmp_path)
    _run(state, rebuild=True)
    job = recorder.jobs[-1]
    assert job.build_platform == "android"
    assert "build:android" in job.capabilities
    # An on-demand build is decided on the worker, so a plain run requires no build token.
    _run(state)
    assert not any(c.startswith("build:") for c in recorder.jobs[-1].capabilities)


def test_a_worker_that_cannot_build_skips_the_run(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    calls: list[Any] = []

    def popen(cmd: Any, **_kw: Any) -> FakeProc:
        calls.append(cmd)
        return FakeProc(["PASS  runs/r/manifest.json\n"])

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=popen
    )
    job = state.register(
        srv.Job(
            cmd=["run"],
            app_path="MyApp.apk",
            build="make apk",
            build_platform="android",
            can_build=False,
        )
    )
    srv.run_job(state, job)
    assert calls == []  # neither the build nor the run spawned
    view = job.view()
    assert view["ok"] is False and view["exitCode"] == 2
    assert any("cannot build android apps" in line for line in view["lines"])


def test_the_worker_reads_its_build_declaration_for_the_job(tmp_path: Path) -> None:
    from bajutsu.serve.server.worker_job import _can_build

    file = tmp_path / "worker.yaml"
    file.write_text("version: 1\ndrivers: [adb]\nbuild: [ios]\n", encoding="utf-8")
    spec = {"build": "make apk", "build_platform": "android"}
    assert _can_build(spec, file) is False
    assert _can_build({**spec, "build_platform": "ios"}, file) is True
    assert _can_build(spec, None) is True  # no --worker-config: the default builds every platform
    assert _can_build({"build": None, "build_platform": None}, file) is True

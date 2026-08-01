"""Tests for `bajutsu serve`'s run-job logic, driving it with an injected Popen."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from typing import Any

import pytest
from _shared import FakeProc, fake_popen, project

from bajutsu import serve as srv
from bajutsu.serve import jobs as srv_jobs
from bajutsu.serve import state as srv_state
from bajutsu.serve.logbus import InMemoryLogBus
from bajutsu.serve.uploads import Upload


def test_run_job_captures_output_and_run_id(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))
    srv.run_job(state, job)
    v = job.view()
    assert v["status"] == "done" and v["exitCode"] == 0 and v["ok"] is True
    assert v["runId"] == "20260610-1"
    assert "step 0 ok" in v["lines"]


def test_run_job_cloud_batch_records_the_manifest_verdict(tmp_path: Path) -> None:
    # A cloud-batch job (BE-0336) runs its scenario on a remote device via the batch seam, not a local
    # subprocess. run_job lands the downloaded run under runs_dir and sets the job's verdict from that
    # run's own manifest — the same pass/fail a local run reports, off the `run`/CI verdict path.
    import json

    from bajutsu.cloud.devicefarm import verdict_from_manifest
    from bajutsu.serve import batch_provider as bp

    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    class _Provider:
        def submit(
            self, request: Any, *, work_dir: Path, dest: Path, checkpoint: Any = None
        ) -> Any:
            run_dir = dest / "runs" / "20260101-1"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"ok": True, "scenarios": [{"scenario": "alpha", "ok": True}]}),
                encoding="utf-8",
            )
            return verdict_from_manifest(dest)

    saved = dict(bp._PROVIDERS)
    bp.register("fake", _Provider())
    try:
        request = bp.BatchRequest(
            provider="fake",
            scenario="smoke.yaml",
            target="demo",
            config=str(cfg),
            platform="ios",
            app_path=str(tmp_path / "app.ipa"),
        )
        job = state.register(srv.Job(batch=request))
        srv.run_job(state, job)
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)

    v = job.view()
    assert v["status"] == "done" and v["exitCode"] == 0 and v["ok"] is True
    assert v["runId"] == "20260101-1"
    # The downloaded run is landed under runs_dir so serve records and renders it like a local run.
    assert (runs / "20260101-1" / "manifest.json").exists()


def test_run_job_cloud_batch_fails_loud_on_an_unknown_provider(tmp_path: Path) -> None:
    # An unknown provider must surface as a failed run (non-zero exit, the error on the transcript),
    # never a silently stranded job (determinism first).
    from bajutsu.serve import batch_provider as bp

    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    request = bp.BatchRequest(
        provider="nope",
        scenario="smoke.yaml",
        target="demo",
        config=str(cfg),
        platform="ios",
        app_path=str(tmp_path / "app.ipa"),
    )
    job = state.register(srv.Job(batch=request))
    srv.run_job(state, job)

    v = job.view()
    assert v["status"] == "done" and v["exitCode"] == 1 and v["ok"] is False
    assert any("unknown batch provider" in line for line in v["lines"])


def test_run_job_cloud_batch_fails_loud_when_submit_raises(tmp_path: Path) -> None:
    # A provider whose submit() raises (e.g. a network or AWS error) must end up as a failed job
    # (status="done", exitCode=1, error on the transcript), never a stranded worker. run_job has no
    # outer except, so an uncaught exception here would leave the job at status="running" forever.
    from bajutsu.serve import batch_provider as bp

    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    class _BrokenProvider:
        def submit(
            self, request: Any, *, work_dir: Path, dest: Path, checkpoint: Any = None
        ) -> Any:
            raise RuntimeError("simulated AWS failure")

    saved = dict(bp._PROVIDERS)
    bp.register("broken", _BrokenProvider())
    try:
        request = bp.BatchRequest(
            provider="broken",
            scenario="smoke.yaml",
            target="demo",
            config=str(cfg),
            platform="ios",
            app_path=str(tmp_path / "app.ipa"),
        )
        job = state.register(srv.Job(batch=request))
        srv.run_job(state, job)
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)

    v = job.view()
    assert v["status"] == "done" and v["exitCode"] == 1 and v["ok"] is False
    assert any("cloud-batch run failed" in line for line in v["lines"])


def test_land_batch_run_does_not_claim_a_colliding_existing_run(tmp_path: Path) -> None:
    # A fresh cloud run id shouldn't collide, but if one already exists under runs_dir it belongs to
    # another run — landing must not claim it (which would point history/rendering at unrelated data)
    # nor overwrite it. It returns None and leaves the existing run untouched.
    import json

    from bajutsu.serve.jobs import _land_batch_run

    download = tmp_path / "download"
    (download / "runs" / "20260101-1").mkdir(parents=True)
    (download / "runs" / "20260101-1" / "manifest.json").write_text(
        json.dumps({"ok": True, "scenarios": []}), encoding="utf-8"
    )
    runs_root = tmp_path / "runs"
    existing = runs_root / "20260101-1"
    existing.mkdir(parents=True)
    (existing / "manifest.json").write_text("EXISTING", encoding="utf-8")

    assert _land_batch_run(download, runs_root) is None
    # The pre-existing run is untouched — not overwritten by the download's manifest.
    assert (existing / "manifest.json").read_text() == "EXISTING"


def test_land_batch_run_warns_when_the_download_carries_more_than_one_manifest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # One scenario is one run is one manifest, so a >1 case means the landed run (a single deterministic
    # pick) can't match the job's verdict, which `verdict_from_manifest` aggregates across every manifest.
    # Land the deterministic first pick, but warn loudly so the disagreement isn't silent.
    import json
    import logging

    from bajutsu.serve.jobs import _land_batch_run

    download = tmp_path / "download"
    for name in ("20260101-1", "20260101-2"):
        (download / "runs" / name).mkdir(parents=True)
        (download / "runs" / name / "manifest.json").write_text(
            json.dumps({"ok": True, "scenarios": []}), encoding="utf-8"
        )
    runs_root = tmp_path / "runs"

    with caplog.at_level(logging.WARNING):
        run_id = _land_batch_run(download, runs_root)

    # The deterministic first pick still lands; the extra manifest is what the warning is about.
    assert run_id == "20260101-1"
    assert any("more than one manifest" in record.message for record in caplog.records)


def test_land_batch_run_ignores_manifests_outside_runs_subdirectory(tmp_path: Path) -> None:
    # _land_batch_run only searches download_dir/runs/ (not the whole tree). A malformed download
    # that has a manifest.json outside that subdirectory (e.g. at the top of download_dir) contains
    # no runs/ directory, so runs_subdir.is_dir() is False, rglob never runs, manifests is empty,
    # and _land_batch_run returns None via the `if not manifests` branch — valid_run_id and
    # shutil.move are never reached.
    import json

    from bajutsu.serve.jobs import _land_batch_run

    download = tmp_path / "download"
    download.mkdir()
    (download / "manifest.json").write_text(
        json.dumps({"ok": True, "scenarios": []}), encoding="utf-8"
    )
    runs_root = tmp_path / "runs"

    result = _land_batch_run(download, runs_root)

    # No runs/ subdirectory → empty manifests → None; nothing lands under runs_root.
    assert result is None
    assert not runs_root.exists() or not any(runs_root.iterdir())


def test_land_batch_run_does_not_land_through_a_symlinked_run_directory(tmp_path: Path) -> None:
    # Path.rglob uses recurse_symlinks=False by default (Python 3.13), so a symlinked run
    # directory under downloads/runs/ is never descended into — its manifest.json is not found,
    # and _land_batch_run returns None via the empty-manifests branch before shutil.move is ever
    # called. This test pins that safety property: the real_dir backing the symlink is untouched.
    import json

    from bajutsu.serve.jobs import _land_batch_run

    download = tmp_path / "download"
    real_dir = tmp_path / "real_run"
    real_dir.mkdir(parents=True)
    (real_dir / "manifest.json").write_text(
        json.dumps({"ok": True, "scenarios": [{"name": "s1", "ok": True}]}), encoding="utf-8"
    )
    runs_subdir = download / "runs"
    runs_subdir.mkdir(parents=True)
    runs_subdir.joinpath("20260101-1").symlink_to(real_dir)
    runs_root = tmp_path / "runs"

    result = _land_batch_run(download, runs_root)

    # rglob did not descend into the symlink, so no manifest was found — None, real_dir untouched.
    assert result is None
    assert real_dir.exists()


def test_record_provenance_merges_not_clobbers(tmp_path: Path) -> None:
    # BE-0090: the run subprocess already wrote a provenance block (scenario fingerprint + the
    # uploadExec decision); serve must merge its upload identity in, not overwrite both away.
    import json

    from bajutsu.serve.jobs import _record_provenance

    runs = tmp_path / "runs"
    run_dir = runs / "20260610-1"
    run_dir.mkdir(parents=True)
    subprocess_block = {"scenarioHash": "sha256:abc", "uploadExec": {"decision": "sandboxed"}}
    (run_dir / "manifest.json").write_text(
        json.dumps({"runId": "20260610-1", "provenance": subprocess_block}), encoding="utf-8"
    )
    state = srv.ServeState(config=None, runs_dir=runs, cwd=tmp_path)
    job = srv.Job(cmd=["x"], run_id="20260610-1", provenance={"source": "upload", "sha256": "z"})
    _record_provenance(state, job)
    prov = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["provenance"]
    assert prov["scenarioHash"] == "sha256:abc"  # subprocess block survives
    assert prov["uploadExec"] == {"decision": "sandboxed"}  # the decision survives
    assert prov["source"] == "upload" and prov["sha256"] == "z"  # serve's identity merged in


def test_run_job_marks_failure(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["FAIL  runs/r/manifest.json\n"], code=1),
    )
    job = state.register(srv.Job(cmd=["x"]))
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
    job = state.register(srv.Job(cmd=["run"], app_path=app_path, build="make build"))
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
    job = state.register(srv.Job(cmd=["run"], app_path="MyApp.app", build="make build"))
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
    job = state.register(srv.Job(cmd=["run"], app_path="MyApp.app", build="make build"))
    srv.run_job(state, job)
    v = job.view()
    assert spawned == [["make", "build"]]  # the run is not spawned when the build fails
    assert v["status"] == "done" and v["ok"] is False and v["exitCode"] == 2
    assert any("build failed" in line for line in v["lines"])


def test_target_build_info_reads_config(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        "    appPath: build/Demo.app\n    build: make demo\n  bare: { bundleId: com.example.bare }\n",
        encoding="utf-8",
    )
    assert srv.target_build_info(cfg, "demo") == ("build/Demo.app", "make demo")
    assert srv.target_build_info(cfg, "bare") == (None, None)  # neither set
    assert srv.target_build_info(cfg, "nope") == (None, None)  # unknown app → no build
    assert srv.target_build_info(tmp_path / "missing.yaml", "demo") == (None, None)  # no config


def test_target_scenarios_dir_reads_config(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "targets:\n  demo: { bundleId: com.example.demo, scenarios: scn/dir }\n"
        "  bare: { bundleId: com.example.bare }\n",
        encoding="utf-8",
    )
    assert srv.target_scenarios_dir(cfg, "demo") == Path("scn/dir")
    assert srv.target_scenarios_dir(cfg, "bare") is None  # unset
    assert srv.target_scenarios_dir(cfg, "nope") is None  # unknown app
    assert srv.target_scenarios_dir(tmp_path / "missing.yaml", "demo") is None  # no config


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
    job = state.register(srv.Job(cmd=["x"], udids=["U1", "U2"]))
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
    job = state.register(srv.Job(cmd=["x"], udids=["BAD"]))
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
    job = state.register(srv.Job(cmd=["run"]))
    srv.run_job(state, job)
    assert terminated, "process was not terminated after stdout error"
    assert job.view()["status"] == "done"


def test_terminate_signals_the_whole_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel must reach the job's children (a record job shells out to `claude -p`): _terminate
    signals the process group, not just the top process."""
    calls: list[Any] = []
    monkeypatch.setattr(srv_jobs.os, "getpgid", lambda pid: pid)  # own session → group == pid
    monkeypatch.setattr(srv_jobs.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))

    class _Proc:
        pid = 4321

        def terminate(self) -> None:
            calls.append("terminate")

    srv_jobs._terminate(_Proc())
    assert calls == [(4321, signal.SIGTERM)]  # group killed; the single-process fallback not used


def test_terminate_falls_back_to_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """When there is no process group (a fake proc / unsupported platform), fall back to terminate()."""
    calls: list[str] = []

    def _no_group(pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(srv_jobs.os, "getpgid", _no_group)
    monkeypatch.setattr(srv_jobs.os, "killpg", lambda *_a: calls.append("killpg"))

    class _Proc:
        pid = 1

        def terminate(self) -> None:
            calls.append("terminate")

    srv_jobs._terminate(_Proc())
    assert calls == ["terminate"]  # killpg failed → fell back to the process


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
    job = state.register(srv.Job(cmd=["run"], app_path="Missing.app", build="make build"))
    srv.run_job(state, job)
    assert terminated, "build process was not terminated after stdout error"
    assert job.view()["status"] == "done"


def test_register_assigns_an_id_and_the_log_bus(tmp_path: Path) -> None:
    # The caller builds a bare Job (no id, no bus); register stamps the sequence id and wires the
    # state's log bus, and the job is then retrievable by that id.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.register(srv.Job(cmd=["x"]))
    assert job.id == "1"
    assert job.bus is state.logbus
    assert state.jobs["1"] is job
    assert state.register(srv.Job(cmd=["y"])).id == "2"  # ids increment


def test_register_rejects_an_already_registered_job(tmp_path: Path) -> None:
    # Registering the same Job twice would orphan its earlier state.jobs entry — guard against it.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.register(srv.Job(cmd=["x"]))
    with pytest.raises(ValueError, match="already registered"):
        state.register(job)


def test_register_does_not_alias_caller_collections(tmp_path: Path) -> None:
    # The registered job must not alias the caller's list/dict (prior new_job semantics): mutating
    # them afterward must not change the job.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    udids = ["U1"]
    materials = {"a.yaml": "x"}
    job = state.register(srv.Job(cmd=["x"], udids=udids, materials=materials))
    udids.append("U2")
    materials["b.yaml"] = "y"
    assert job.udids == ["U1"]
    assert job.materials == {"a.yaml": "x"}


def test_try_new_job_caps_concurrency_per_user(tmp_path: Path) -> None:
    # A per-user cap stops one user from monopolizing the scarce device pool (BE-0015 7c-3).
    state = srv.ServeState(runs_dir=tmp_path / "runs", max_concurrent_per_user=1)
    assert state.try_register(srv.Job(cmd=[], actor="alice")) is not None  # alice's first job
    assert state.try_register(srv.Job(cmd=[], actor="alice")) is None  # alice is at her cap
    assert (
        state.try_register(srv.Job(cmd=[], actor="bob")) is not None
    )  # a different user is unaffected


def test_try_new_job_per_user_cap_ignores_anonymous_jobs(tmp_path: Path) -> None:
    # Token/anonymous jobs (no identity) aren't subject to the per-user cap — only the global one.
    state = srv.ServeState(runs_dir=tmp_path / "runs", max_concurrent_per_user=1)
    assert state.try_register(srv.Job(cmd=[], actor=None)) is not None
    assert state.try_register(srv.Job(cmd=[], actor=None)) is not None


def test_try_new_job_per_user_unlimited_by_default(tmp_path: Path) -> None:
    # Default 0 = unlimited, so behavior is unchanged unless an operator opts in.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    assert state.try_register(srv.Job(cmd=[], actor="alice")) is not None
    assert state.try_register(srv.Job(cmd=[], actor="alice")) is not None


def test_try_new_job_caps_concurrency_per_org(tmp_path: Path) -> None:
    # A per-org cap keeps one tenant from monopolizing the scarce Mac pool, even when its users each
    # stay under the per-user cap (BE-0016 Tier B pool fairness).
    state = srv.ServeState(runs_dir=tmp_path / "runs", max_concurrent_per_org=1)
    assert state.try_register(srv.Job(cmd=[], org="acme")) is not None  # acme's first job
    assert state.try_register(srv.Job(cmd=[], org="acme")) is None  # acme is at its org cap
    assert (
        state.try_register(srv.Job(cmd=[], org="globex")) is not None
    )  # a different org is unaffected


def test_try_new_job_per_org_unlimited_by_default(tmp_path: Path) -> None:
    # Default 0 = unlimited, so a single-tenant deploy (every job in the default org) is unchanged.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    assert state.try_register(srv.Job(cmd=[], org="acme")) is not None
    assert state.try_register(srv.Job(cmd=[], org="acme")) is not None


def test_try_new_job_per_user_and_per_org_caps_compose(tmp_path: Path) -> None:
    # Both caps apply: a job is registered only when under its user's cap and its org's cap.
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", max_concurrent_per_user=1, max_concurrent_per_org=2
    )
    assert state.try_register(srv.Job(cmd=[], actor="alice", org="acme")) is not None
    # bob is a second acme user (under the org cap of 2), his own first job (under the per-user cap).
    assert state.try_register(srv.Job(cmd=[], actor="bob", org="acme")) is not None
    # carol would be acme's third in-flight job — blocked by the org cap even though it's her first.
    assert state.try_register(srv.Job(cmd=[], actor="carol", org="acme")) is None
    # alice's second job is blocked by her per-user cap, regardless of org headroom.
    assert state.try_register(srv.Job(cmd=[], actor="alice", org="globex")) is None


def test_try_register_forwards_the_per_call_device_budget(tmp_path: Path) -> None:
    # The device budget is per-target, not a state-wide field like the other caps, so `try_register`
    # takes it per call and forwards it to the registry's batch pool cap (BE-0336 Unit 4).
    from bajutsu.serve.batch_provider import BatchRequest

    request = BatchRequest(
        provider="devicefarm",
        scenario="scenarios/s.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path="app.apk",
    )
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    assert state.try_register(srv.Job(batch=request), device_budget=1) is not None
    assert state.try_register(srv.Job(batch=request), device_budget=1) is None
    # With no budget passed (the default), the batch pool is unbounded — every other run path.
    assert state.try_register(srv.Job(batch=request)) is not None


# The concurrency logic lives in JobRegistry (BE-0198), so it is tested directly against the
# registry — the id sequence, the register-twice guard, and each cap — without standing up a full
# ServeState, whose __post_init__ resolves stores, secrets, and a launch dir that the caps don't
# depend on.


def test_job_registry_assigns_monotonic_ids_and_the_log_bus() -> None:
    # The registry is the sole owner of the id sequence: ids increment from "1", each registered job
    # is wired to the registry's log bus, and it is then retrievable by that id.
    bus = InMemoryLogBus()
    reg = srv_state.JobRegistry(logbus=bus)
    first = reg.register(srv.Job(cmd=["x"]))
    second = reg.register(srv.Job(cmd=["y"]))
    assert (first.id, second.id) == ("1", "2")
    assert first.bus is bus
    assert reg.jobs["1"] is first


def test_job_registry_rejects_an_already_registered_job() -> None:
    # Registering a job that already has an id would orphan its earlier entry — guard against it.
    reg = srv_state.JobRegistry(logbus=InMemoryLogBus())
    job = reg.register(srv.Job(cmd=["x"]))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(job)


def test_job_registry_caps_concurrency_globally() -> None:
    # The global cap rejects once the running count reaches it (BE-0051).
    reg = srv_state.JobRegistry(logbus=InMemoryLogBus())
    assert reg.try_register(srv.Job(cmd=[]), max_concurrent=1) is not None
    assert reg.try_register(srv.Job(cmd=[]), max_concurrent=1) is None


def test_job_registry_caps_concurrency_per_user() -> None:
    # The per-user cap is scoped to an identified actor; a different actor is unaffected (BE-0015 7c-3).
    reg = srv_state.JobRegistry(logbus=InMemoryLogBus())
    assert reg.try_register(srv.Job(cmd=[], actor="alice"), max_concurrent_per_user=1) is not None
    assert reg.try_register(srv.Job(cmd=[], actor="alice"), max_concurrent_per_user=1) is None
    assert reg.try_register(srv.Job(cmd=[], actor="bob"), max_concurrent_per_user=1) is not None


def test_job_registry_caps_concurrency_per_org() -> None:
    # The per-org cap is scoped to a job's org; a different org is unaffected (BE-0016 Tier B).
    reg = srv_state.JobRegistry(logbus=InMemoryLogBus())
    assert reg.try_register(srv.Job(cmd=[], org="acme"), max_concurrent_per_org=1) is not None
    assert reg.try_register(srv.Job(cmd=[], org="acme"), max_concurrent_per_org=1) is None
    assert reg.try_register(srv.Job(cmd=[], org="globex"), max_concurrent_per_org=1) is not None


def test_job_registry_caps_concurrent_batch_by_provider_pool() -> None:
    # The device budget caps in-flight cloud-batch jobs keyed on the batch device pool — the
    # provider — so at most K reserve a device at once (BE-0336 Unit 4). A run on a different
    # provider draws on a different pool and is unaffected; a non-batch job is exempt entirely.
    from bajutsu.serve.batch_provider import BatchRequest

    def _batch(provider: str) -> BatchRequest:
        return BatchRequest(
            provider=provider,
            scenario="scenarios/s.yaml",
            target="demo",
            config="bajutsu.config.yaml",
            platform="android",
            app_path="app.apk",
        )

    reg = srv_state.JobRegistry(logbus=InMemoryLogBus())
    assert reg.try_register(srv.Job(batch=_batch("devicefarm")), max_concurrent_batch=1) is not None
    # A second devicefarm run would reserve a second device beyond the budget of 1 — rejected.
    assert reg.try_register(srv.Job(batch=_batch("devicefarm")), max_concurrent_batch=1) is None
    # A different provider is a different device pool, so its own budget of 1 still has room.
    assert reg.try_register(srv.Job(batch=_batch("firebase")), max_concurrent_batch=1) is not None
    # A non-batch (local) job reserves no cloud device, so the batch budget never applies to it.
    assert reg.try_register(srv.Job(cmd=[]), max_concurrent_batch=1) is not None


def test_job_registry_batch_budget_unlimited_by_default() -> None:
    # Default 0 = unbounded, so a target with no configured device budget dispatches every scenario
    # as before (BE-0336 Unit 4). A cap <= 0 is likewise treated as unlimited.
    from bajutsu.serve.batch_provider import BatchRequest

    request = BatchRequest(
        provider="devicefarm",
        scenario="scenarios/s.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path="app.apk",
    )
    reg = srv_state.JobRegistry(logbus=InMemoryLogBus())
    assert reg.try_register(srv.Job(batch=request)) is not None
    assert reg.try_register(srv.Job(batch=request)) is not None
    assert reg.try_register(srv.Job(batch=request), max_concurrent_batch=0) is not None


def test_job_registry_counts_only_running_jobs() -> None:
    # active_jobs and in_flight_by_org count only running jobs; a finished job drops out of both.
    reg = srv_state.JobRegistry(logbus=InMemoryLogBus())
    reg.register(srv.Job(cmd=[], org="acme"))
    reg.register(srv.Job(cmd=[], org="acme"))
    reg.register(srv.Job(cmd=[], org="beta"))
    reg.register(srv.Job(cmd=[], org="acme")).status = "done"
    assert reg.active_jobs() == 3
    assert reg.in_flight_by_org() == {"acme": 2, "beta": 1}


def _bundle(uploads_dir: Path, name: str) -> Upload:
    """An Upload backed by a real extraction dir under *uploads_dir* (BE-0073)."""
    d = uploads_dir / name
    d.mkdir(parents=True)
    cfg = d / "bajutsu.config.yaml"
    cfg.write_text("targets: {}\n", encoding="utf-8")
    return Upload(dir=d, config=cfg, filename=f"{name}.zip", sha256="x", size=1, org="default")


def test_bind_upload_points_config_and_cwd_at_the_bundle(tmp_path: Path) -> None:
    # Binding a bundle makes it the active config: `config`/`cwd` point into the extracted tree, so
    # the normal run/record/crawl flow resolves the bundle's relative paths (BE-0073).
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, uploads_dir=uploads)
    up = _bundle(uploads, "u1")
    state.bind_upload(up)
    assert state.upload is up
    assert state.config == up.config and state.cwd == up.root


def test_bind_upload_replaces_the_previous_bundle(tmp_path: Path) -> None:
    # Only one bundle is *bound* at a time, but binding a second no longer removes the first's
    # extraction dir (BE-0243): it is now a durable, sha256-keyed cache entry, independent of any
    # single bind, the same way binding a different Git source never sweeps the Git checkout cache.
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, uploads_dir=uploads)
    first = _bundle(uploads, "first")
    state.bind_upload(first)
    second = _bundle(uploads, "second")
    state.bind_upload(second)
    assert state.upload is second and first.dir.exists()
    assert second.dir.exists()


def test_release_upload_keeps_the_cache_and_resets_cwd(tmp_path: Path) -> None:
    # Switching away from a bundle (any other config source) drops the *binding* and restores cwd to
    # serve's launch dir, so the file-browser/Git sources don't inherit a stale bundle cwd — but the
    # extraction cache dir itself persists (BE-0243), ready for reuse.
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, uploads_dir=uploads)
    up = _bundle(uploads, "u1")
    state.bind_upload(up)
    state.release_upload()
    assert state.upload is None and up.dir.exists()
    assert state.cwd == state.base_cwd == tmp_path


class _CapturingProvider:
    """A batch provider that records the checkpoint it was handed and lands a passing run."""

    def __init__(self) -> None:
        self.checkpoint: Any = "unset"

    def submit(self, request: Any, *, work_dir: Path, dest: Path, checkpoint: Any = None) -> Any:
        import json

        from bajutsu.cloud.devicefarm import verdict_from_manifest

        self.checkpoint = checkpoint
        run_dir = dest / "runs" / "20260101-1"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            json.dumps({"ok": True, "scenarios": [{"scenario": "alpha", "ok": True}]}),
            encoding="utf-8",
        )
        return verdict_from_manifest(dest)


def _batch_state(tmp_path: Path, provider: Any, **kwargs: Any) -> tuple[srv.ServeState, srv.Job]:
    from bajutsu.serve import batch_provider as bp

    scn_dir, cfg, runs = project(tmp_path)
    bp.register("fake", provider)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, **kwargs)
    request = bp.BatchRequest(
        provider="fake",
        scenario="smoke.yaml",
        target="demo",
        config=str(cfg),
        platform="ios",
        app_path=str(tmp_path / "app.ipa"),
    )
    job = state.register(srv.Job(batch=request))
    return state, job


def test_batch_job_gets_a_durable_checkpoint_on_the_db_backend(
    tmp_path: Path, serve_engine: Any
) -> None:
    # On the hosted DB backend the worker hands the provider a durable checkpoint, so a 150-minute
    # poll interrupted by a serve restart resumes the scheduled run rather than losing it (Unit 5).
    from bajutsu.serve import batch_provider as bp
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    provider = _CapturingProvider()
    saved = dict(bp._PROVIDERS)
    try:
        state, job = _batch_state(tmp_path, provider, repository=SqlRepository(engine))
        srv.run_job(state, job)
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)

    assert provider.checkpoint is not None
    assert job.view()["ok"] is True


def test_batch_job_has_no_durable_checkpoint_on_the_local_backend(tmp_path: Path) -> None:
    # A local single-process backend has no database to persist to, so the checkpoint is None: its
    # best-effort path is the in-process retry, never a cross-restart resume (BE-0336 Unit 5).
    from bajutsu.serve import batch_provider as bp

    provider = _CapturingProvider()
    saved = dict(bp._PROVIDERS)
    try:
        state, job = _batch_state(tmp_path, provider)
        srv.run_job(state, job)
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)

    assert provider.checkpoint is None
    assert job.view()["ok"] is True


def test_batch_run_arn_persists_across_a_repository_reload(serve_engine: Any) -> None:
    # The scheduled run ARN persists in the jobs table, so a fresh repository over the same database
    # (a serve restart) still resolves it — the durable state resume relies on (BE-0336 Unit 5).
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.enqueue_job("j1", "o", {}, capabilities=[])

    assert repo.load_batch_run_arn("j1") is None
    repo.save_batch_run_arn("j1", "arn:run/xyz")

    assert SqlRepository(engine).load_batch_run_arn("j1") == "arn:run/xyz"

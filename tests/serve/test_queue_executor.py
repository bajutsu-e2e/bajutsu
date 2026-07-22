"""Tests for the queue-based RunExecutor + worker entrypoint (BE-0015 server phase).

`QueueExecutor` is a server implementation of the `RunExecutor` seam: instead of running `run_job`
in-process (like `LocalExecutor`), it serializes the job and enqueues it; a remote `bajutsu worker`
later reconstructs the job and runs the *unchanged* `run_job`. These tests drive both ends with a
fake queue and an injected Popen, so the Linux gate needs neither Redis nor RQ installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _shared import SCENARIO, FakeProc, fake_popen, project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.executor import QueueExecutor
from bajutsu.serve.server.worker_job import execute_job_spec, job_spec


class _FakeQueue:
    """Records enqueue calls; stands in for an RQ Queue so tests need no Redis."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[Any, tuple[Any, ...]]] = []

    def enqueue(self, func: Any, *args: Any, **_kw: Any) -> None:
        self.enqueued.append((func, args))


def test_dispatch_enqueues_a_serializable_job_spec(tmp_path: Path) -> None:
    q = _FakeQueue()
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.register(
        srv.Job(
            cmd=["python", "-m", "bajutsu", "run", "--config", "c.yaml"],
            udids=["U1"],
            app_path="A.app",
            build="make build",
        )
    )
    QueueExecutor(q).dispatch(state, job)

    assert len(q.enqueued) == 1
    func, args = q.enqueued[0]
    assert func is execute_job_spec  # RQ enqueues the worker entrypoint by reference
    spec = args[0]
    assert spec == {
        "job_id": job.id,
        "cmd": ["python", "-m", "bajutsu", "run", "--config", "c.yaml"],
        "udids": ["U1"],
        "app_path": "A.app",
        "build": "make build",
        "materials": {},  # no materials for a local-built job
        "out_path": None,  # not a record job
        "record_save": None,
        "materialize_baselines": False,
        "org": "default",  # single-tenant default org (BE-0015 multi-tenancy)
        "actor": None,  # no OAuth identity for this locally-built job
        "evidence_prefix": "",  # no per-run evidence prefix requested (BE-0110)
        "project_id": None,  # no project hub wired for this locally-built job (BE-0225)
        "env_overlay": {},  # no AI provider selected for this org, so no overlay (BE-0229)
    }
    json.dumps(spec)  # must carry no live objects (locks/Popen/bus) — JSON round-trips


def test_job_spec_round_trips_through_json(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.register(srv.Job(cmd=["run"], udids=["A", "B"], app_path=None, build=None))
    spec = job_spec(job)
    assert json.loads(json.dumps(spec)) == spec


def test_job_spec_carries_the_actor(tmp_path: Path) -> None:
    # The actor travels so the worker can attribute the recorded run to the user (BE-0015).
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.register(srv.Job(cmd=["run"], actor="alice", org="acme"))
    spec = job_spec(job)
    assert spec["actor"] == "alice"
    assert spec["org"] == "acme"


def test_job_spec_carries_the_evidence_prefix(tmp_path: Path) -> None:
    # The per-run evidence prefix travels so the worker relays it when requesting presigned PUT URLs,
    # landing the run's evidence under the lifecycle path CI chose (BE-0110).
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.register(srv.Job(cmd=["run"], evidence_prefix="main/abc1234/"))
    assert job_spec(job)["evidence_prefix"] == "main/abc1234/"


def test_job_spec_carries_the_project_id(tmp_path: Path) -> None:
    # The project resolved at enqueue travels so the worker's `_persist_run` stamps `runs.project_id`
    # without a registry of its own (BE-0225 unit 3, unit 2 review carry-over).
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.register(srv.Job(cmd=["run"], project_id="proj-1"))
    assert job_spec(job)["project_id"] == "proj-1"


def test_execute_job_spec_stamps_the_project_id_carried_in_the_spec(tmp_path: Path) -> None:
    # The worker has no project registry; the run must still be labeled with the project the control
    # plane resolved at enqueue and shipped in the spec (BE-0225 unit 3).
    from sqlalchemy import create_engine

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    project(tmp_path)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("acme", slug="acme", name="acme")
    repo.create_project(_project_record("proj-1", "acme", "checkout"))

    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "run"],
        "udids": [],
        "app_path": None,
        "build": None,
        "org": "acme",
        "project_id": "proj-1",
    }
    execute_job_spec(
        spec,
        popen=fake_popen(["PASS  runs/20260610-2/manifest.json\n"]),
        cwd=tmp_path,
        bus=srv.InMemoryLogBus(),
        repository=repo,
    )
    rec = repo.get_run("20260610-2")
    assert rec is not None and rec.project_id == "proj-1"


def _project_record(pid: str, org_id: str, name: str) -> Any:
    from bajutsu.serve.server.db import ProjectRecord

    return ProjectRecord(id=pid, org_id=org_id, name=name, source=None)


def test_start_run_carries_a_valid_evidence_prefix_end_to_end(tmp_path: Path) -> None:
    # A valid evidence_prefix on /api/run reaches the enqueued job spec, so the worker can relay it.
    scn_dir, cfg, runs = project(tmp_path)
    q = _FakeQueue()
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, executor=QueueExecutor(q)
    )
    _, code = ops.start_run(
        state, {"scenario": "smoke.yaml", "target": "demo", "evidence_prefix": "main/abc1234/"}
    )
    assert code == 200
    assert q.enqueued[0][1][0]["evidence_prefix"] == "main/abc1234/"


def test_start_run_derives_the_jobs_required_capabilities(tmp_path: Path) -> None:
    # BE-0166: the dispatched job carries the target's required capabilities (its platform axis plus
    # any `requires:`), so the DB router leases it only to a capable worker. `demo` is an iOS
    # target and declares `requires: [ios18]`.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir}, requires: [ios18] }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()

    dispatched: list[Any] = []

    class _CapturingExecutor:
        def dispatch(self, state: Any, job: Any) -> None:
            dispatched.append(job)

    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        executor=_CapturingExecutor(),
    )
    _, code = ops.start_run(state, {"scenario": "smoke.yaml", "target": "demo"})
    assert code == 200
    assert dispatched[0].capabilities == ["ios18", "platform:ios"]


def test_execute_job_spec_records_the_run_into_an_injected_repository(tmp_path: Path) -> None:
    # On the server backend the run executes on the worker; with a repository wired (the worker has
    # BAJUTSU_DATABASE_URL), the finished run is recorded under its org and actor (BE-0015).
    from sqlalchemy import create_engine

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    project(tmp_path)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("acme", slug="acme", name="acme")
    repo.upsert_user("alice", org_id="acme", github_login="alice", email="a@x")

    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "run"],
        "udids": [],
        "app_path": None,
        "build": None,
        "actor": "alice",
        "org": "acme",
    }
    execute_job_spec(
        spec,
        popen=fake_popen(["PASS  runs/20260610-1/manifest.json\n"]),
        cwd=tmp_path,
        bus=srv.InMemoryLogBus(),
        repository=repo,
    )
    rec = repo.get_run("20260610-1")
    assert rec is not None
    assert rec.org_id == "acme"
    assert rec.created_by == "alice"
    assert rec.status == "done"


def test_execute_job_spec_rebuilds_and_runs_run_job(tmp_path: Path) -> None:
    # The worker reconstructs a Job + minimal ServeState from the spec and runs the unchanged
    # run_job; an injected Popen stands in for the real `bajutsu run` subprocess.
    project(tmp_path)
    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    job = execute_job_spec(
        spec,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
        cwd=tmp_path,
        bus=srv.InMemoryLogBus(),  # inject a bus so the gate needs no redis (default is Redis)
    )
    v = job.view()
    assert v["status"] == "done" and v["ok"] is True and v["runId"] == "20260610-1"
    assert "step 0 ok" in v["lines"]


def test_execute_job_spec_streams_logs_to_the_injected_bus(tmp_path: Path) -> None:
    project(tmp_path)
    bus = srv.InMemoryLogBus()
    spec = {"job_id": "7", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    execute_job_spec(
        spec,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
        cwd=tmp_path,
        bus=bus,
    )
    replay = list(bus.stream("7"))
    assert "step 0 ok" in replay
    assert any("20260610-1" in line for line in replay)


def test_worker_operational_log_correlates_the_job_and_run(tmp_path: Path) -> None:
    # The worker's own operational trace (BE-0055) tags every record with the job's ids, and emits
    # the start/finish events — distinct from the run *output* it streams to the LogBus.
    import io

    from bajutsu.serve import oplog

    project(tmp_path)
    buf = io.StringIO()
    oplog.configure(fmt="json", level="INFO", stream=buf)
    try:
        spec = {
            "job_id": "42",
            "cmd": ["bajutsu", "run"],
            "udids": [],
            "app_path": None,
            "build": None,
        }
        execute_job_spec(
            spec,
            popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
            cwd=tmp_path,
            bus=srv.InMemoryLogBus(),
        )
    finally:
        oplog.reset()
    records = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    events = {r.get("event"): r for r in records if r.get("event")}
    assert "worker.job.started" in events
    assert events["worker.job.started"]["job_id"] == "42"
    assert events["worker.job.finished"]["job_id"] == "42"
    assert events["worker.job.finished"]["run_id"] == "20260610-1"


def test_execute_job_spec_records_terminal_status_on_the_bus(tmp_path: Path) -> None:
    project(tmp_path)
    bus = srv.InMemoryLogBus()
    spec = {"job_id": "9", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    execute_job_spec(
        spec,
        popen=fake_popen(["PASS  runs/20260610-1/manifest.json\n"]),
        cwd=tmp_path,
        bus=bus,
    )
    final = bus.final("9")
    assert final is not None
    view = json.loads(final)
    assert view["status"] == "done" and view["ok"] is True and view["runId"] == "20260610-1"
    assert "lines" not in view


def test_execute_job_spec_materializes_files_into_the_workspace(tmp_path: Path) -> None:
    # The worker writes the control-plane-shipped scenario + config into its workspace before the
    # run, confined to it (an escaping material is ignored).
    project(tmp_path)
    spec = {
        "job_id": "m",
        "cmd": ["bajutsu", "run"],
        "udids": [],
        "app_path": None,
        "build": None,
        "materials": {
            "scenarios/smoke.yaml": "- name: a\n  steps: []\n",
            "bajutsu.config.yaml": "targets: {demo: {bundleId: x}}\n",
            "../escape.yaml": "nope",  # must not be written outside the workspace
            "": "root",  # resolves to the workspace root — must be ignored, not write_text a dir
            "scenarios/..": "root2",  # also the workspace root via traversal — ignored
        },
    }
    execute_job_spec(spec, popen=fake_popen(["ok\n"]), cwd=tmp_path, bus=srv.InMemoryLogBus())
    assert (tmp_path / "scenarios/smoke.yaml").read_text() == "- name: a\n  steps: []\n"
    assert (tmp_path / "bajutsu.config.yaml").exists()
    assert not (tmp_path.parent / "escape.yaml").exists()  # confinement held
    assert tmp_path.is_dir()  # the root-resolving materials didn't crash or clobber the workspace


def test_start_run_on_the_server_backend_materializes_scenario_and_config(tmp_path: Path) -> None:
    # A server-backend run enqueues a spec whose cmd uses workspace-relative paths and whose
    # materials carry the scenario + config text — so a remote worker can run without the project.
    from bajutsu.serve.server.scenarios import StorageScenarioStore

    scn_dir, cfg, runs = project(tmp_path)
    scenario_text = (scn_dir / "smoke.yaml").read_text()
    q = _FakeQueue()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path, executor=QueueExecutor(q))
    state.scenarios = StorageScenarioStore(
        FakeScenarioStorage({"demo": {"smoke.yaml": scenario_text}})
    )

    payload, code = ops.start_run(state, {"scenario": "smoke.yaml", "target": "demo"})
    assert code == 200 and "jobId" in payload

    spec = q.enqueued[0][1][0]
    cmd = spec["cmd"]
    assert "scenarios/smoke.yaml" in cmd  # workspace-relative --scenario
    assert "bajutsu.config.yaml" in cmd  # workspace-relative --config
    assert "baselines" in cmd  # workspace-relative --baselines (materialized on the worker)
    assert spec["materialize_baselines"] is True
    assert spec["materials"]["scenarios/smoke.yaml"] == scenario_text
    assert "targets:" in spec["materials"]["bajutsu.config.yaml"]


def test_start_run_passes_safe_backfilled_flags_and_withholds_host_paths(tmp_path: Path) -> None:
    # BE-0134: the flags run_command couldn't previously pass through now flow from the request
    # body — except client-supplied host directory paths (schemas/goldens), which BE-0051 keeps out
    # of a serve-driven argv (baselines is serve-computed for the same reason).
    from bajutsu.serve.server.scenarios import StorageScenarioStore

    scn_dir, cfg, runs = project(tmp_path)
    scenario_text = (scn_dir / "smoke.yaml").read_text()
    q = _FakeQueue()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path, executor=QueueExecutor(q))
    state.scenarios = StorageScenarioStore(
        FakeScenarioStorage({"demo": {"smoke.yaml": scenario_text}})
    )
    _payload, code = ops.start_run(
        state,
        {
            "scenario": "smoke.yaml",
            "target": "demo",
            "tag": "smoke",
            "exclude": "slow",
            "browser": "firefox",
            "browsers": "chromium,firefox",
            "network": False,
            "zip": True,
            "alertInstruction": "tap Allow",
            "logPredicate": "subsystem == 'x'",
            "logSubsystem": "com.example",
            "schemas": "/etc",  # host path — must be withheld (BE-0051)
            "goldens": "/tmp/g",  # host path — must be withheld (BE-0051)
        },
    )
    assert code == 200
    cmd = q.enqueued[0][1][0]["cmd"]
    assert cmd[cmd.index("--tag") + 1] == "smoke"
    assert cmd[cmd.index("--exclude") + 1] == "slow"
    assert cmd[cmd.index("--browser") + 1] == "firefox"
    assert cmd[cmd.index("--browsers") + 1] == "chromium,firefox"
    assert "--no-network" in cmd  # network=False forces the off side of the pair
    assert "--zip" in cmd
    assert cmd[cmd.index("--alert-instruction") + 1] == "tap Allow"
    assert cmd[cmd.index("--log-predicate") + 1] == "subsystem == 'x'"
    assert cmd[cmd.index("--log-subsystem") + 1] == "com.example"
    assert "--schemas" not in cmd and "--goldens" not in cmd
    # Git-config knobs stay config-driven too — start_run never sources them from the client body.
    assert "--config-offline" not in cmd and "--require-pinned-config" not in cmd


class FakeScenarioStorage:
    """Per-project scenario storage, in memory: {app: {ref: yaml}} (for the start_run test)."""

    def __init__(self, projects: dict[str, dict[str, str]]) -> None:
        self._projects = projects

    def has_app(self, app: str) -> bool:
        return app in self._projects

    def list(self, app: str) -> list[dict[str, object]]:
        return [{"file": r, "path": r} for r in sorted(self._projects.get(app, {}))]

    def read(self, app: str, ref: str | None) -> str | None:
        return self._projects.get(app, {}).get(ref or "")

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        return ref


class _RecordingWorkerIO:
    """In-memory `WorkerIO` for the worker upload tests (BE-0160): records the calls execute_job_spec
    makes, so the gate exercises the orchestration without a network or a cloud SDK."""

    def __init__(self) -> None:
        self.downloaded: list[Path] = []
        self.uploaded: list[tuple[Path, str]] = []
        self.saved: list[tuple[Path, str, str, str]] = []

    def download_baselines(self, work: Path) -> None:
        self.downloaded.append(work)

    def upload_run(self, work: Path, run_id: str) -> None:
        self.uploaded.append((work, run_id))

    def save_scenario(self, work: Path, out_path: str, app: str, ref: str) -> None:
        self.saved.append((work, out_path, app, ref))


def test_execute_job_spec_uploads_the_run_tree_through_the_io(tmp_path: Path) -> None:
    # After the run, the worker uploads its run tree through the presigned-URL seam, scoped to the
    # run id the run minted (the key layout is fixed server-side, not by the worker — BE-0160).
    def popen_writing_run(_cmd: list[str], **_kw: object) -> FakeProc:
        run = tmp_path / "runs" / "20260610-1"
        run.mkdir(parents=True, exist_ok=True)
        (run / "report.html").write_text("<html>", encoding="utf-8")
        return FakeProc(["PASS  runs/20260610-1/manifest.json\n"])

    io = _RecordingWorkerIO()
    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    execute_job_spec(spec, popen=popen_writing_run, cwd=tmp_path, bus=srv.InMemoryLogBus(), io=io)
    assert io.uploaded == [(tmp_path, "20260610-1")]


def test_execute_job_spec_skips_upload_without_an_io(tmp_path: Path) -> None:
    # No WorkerIO injected (a worker with no hosted control plane) -> a finished run isn't failed; it
    # just doesn't upload.
    project(tmp_path)
    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    job = execute_job_spec(spec, popen=fake_popen(["ok\n"]), cwd=tmp_path, bus=srv.InMemoryLogBus())
    assert job.view()["status"] == "done"  # ran fine, just no upload


def test_execute_job_spec_saves_the_authored_scenario_through_the_io(tmp_path: Path) -> None:
    # A server-backend `record`: after authoring, the worker persists the scenario through the seam
    # as (app, ref) — the presigned scenario URL, not a direct object-store write (BE-0160).
    def popen_writing_scenario(_cmd: list[str], **_kw: object) -> FakeProc:
        out = tmp_path / "scenarios" / "login.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("- name: login\n  steps: []\n", encoding="utf-8")
        return FakeProc(["authored ok\n"])

    io = _RecordingWorkerIO()
    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "record"],
        "udids": [],
        "app_path": None,
        "build": None,
        "out_path": "scenarios/login.yaml",
        "record_save": ["demo", "login.yaml"],
    }
    job = execute_job_spec(
        spec, popen=popen_writing_scenario, cwd=tmp_path, bus=srv.InMemoryLogBus(), io=io
    )
    assert io.saved == [(tmp_path, "scenarios/login.yaml", "demo", "login.yaml")]
    assert job.view()["outPath"] == "scenarios/login.yaml"  # terminal status reports the out path


def test_execute_job_spec_materializes_baselines_through_the_io(tmp_path: Path) -> None:
    # A server-backend run downloads its visual baselines through the seam before running, so the
    # cmd's `--baselines baselines` resolves on the worker (BE-0160).
    io = _RecordingWorkerIO()
    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "run"],
        "udids": [],
        "app_path": None,
        "build": None,
        "materialize_baselines": True,
    }
    execute_job_spec(
        spec, popen=fake_popen(["ok\n"]), cwd=tmp_path, bus=srv.InMemoryLogBus(), io=io
    )
    assert io.downloaded == [tmp_path]


def test_execute_job_spec_surfaces_an_upload_failure(tmp_path: Path) -> None:
    # An upload failure must fail the job loudly — a report the control plane can't serve is a
    # failure, not a silent skip (BE-0160 / determinism-first).
    class _FailingIO(_RecordingWorkerIO):
        def upload_run(self, work: Path, run_id: str) -> None:
            raise RuntimeError("upload boom")

    def popen_writing_run(_cmd: list[str], **_kw: object) -> FakeProc:
        (tmp_path / "runs" / "20260610-1").mkdir(parents=True, exist_ok=True)
        return FakeProc(["PASS  runs/20260610-1/manifest.json\n"])

    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    with pytest.raises(RuntimeError, match="upload boom"):
        execute_job_spec(
            spec, popen=popen_writing_run, cwd=tmp_path, bus=srv.InMemoryLogBus(), io=_FailingIO()
        )


def test_start_record_on_the_server_backend_materializes_and_targets_storage(
    tmp_path: Path,
) -> None:
    from bajutsu.serve.server.scenarios import StorageScenarioStore

    _scn, cfg, runs = project(tmp_path)
    q = _FakeQueue()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path, executor=QueueExecutor(q))
    state.scenarios = StorageScenarioStore(FakeScenarioStorage({"demo": {}}))

    payload, code = ops.start_record(state, {"target": "demo", "goal": "log in", "name": "login"})
    assert code == 200 and payload["path"] == "login.yaml"

    spec = q.enqueued[0][1][0]
    assert "scenarios/login.yaml" in spec["cmd"]  # workspace-relative --out
    assert "bajutsu.config.yaml" in spec["cmd"]  # config materialized
    assert spec["record_save"] == ["demo", "login.yaml"]
    assert "targets:" in spec["materials"]["bajutsu.config.yaml"]


def test_execute_job_spec_skips_baseline_download_when_not_requested(tmp_path: Path) -> None:
    # A run that doesn't materialize baselines never touches the seam's download path (BE-0160).
    io = _RecordingWorkerIO()
    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "run"],
        "udids": [],
        "app_path": None,
        "build": None,
        "materialize_baselines": False,
    }
    execute_job_spec(
        spec, popen=fake_popen(["ok\n"]), cwd=tmp_path, bus=srv.InMemoryLogBus(), io=io
    )
    assert io.downloaded == []

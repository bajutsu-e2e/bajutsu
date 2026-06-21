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
from _shared import FakeProc, fake_popen, project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server import worker_job
from bajutsu.serve.server.executor import QueueExecutor
from bajutsu.serve.server.logbus import RedisLogBus
from bajutsu.serve.server.worker_job import execute_job_spec, job_spec


class _FakeRedis:
    """The slice of a Redis client RedisLogBus uses, in memory. Returns the stored values as
    `object` (bytes, like redis-py) to match the `RedisLike` protocol's `lrange -> list[object]`.
    A DB stand-in so the worker's cross-process log flow is exercised without a real Redis."""

    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}
        self._kv: dict[str, str] = {}

    def rpush(self, key: str, value: str) -> int:
        self._lists.setdefault(key, []).append(value)
        return len(self._lists[key])

    def lrange(self, key: str, start: int, end: int) -> list[object]:
        items = self._lists.get(key, [])
        stop = len(items) if end == -1 else end + 1
        return [s.encode() for s in items[start:stop]]

    def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    def get(self, key: str) -> bytes | None:
        v = self._kv.get(key)
        return v.encode() if v is not None else None

    def expire(self, key: str, seconds: int) -> None:
        pass  # close() bounds key lifetime; this fake keeps everything for the test's duration


class _FakeQueue:
    """Records enqueue calls; stands in for an RQ Queue so tests need no Redis."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[Any, tuple[Any, ...]]] = []

    def enqueue(self, func: Any, *args: Any, **_kw: Any) -> None:
        self.enqueued.append((func, args))


def test_dispatch_enqueues_a_serializable_job_spec(tmp_path: Path) -> None:
    q = _FakeQueue()
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.new_job(
        ["python", "-m", "bajutsu", "run", "--config", "c.yaml"],
        udids=["U1"],
        app_path="A.app",
        build="make build",
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
    }
    json.dumps(spec)  # must carry no live objects (locks/Popen/bus) — JSON round-trips


def test_job_spec_round_trips_through_json(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.new_job(["run"], udids=["A", "B"], app_path=None, build=None)
    spec = job_spec(job)
    assert json.loads(json.dumps(spec)) == spec


def test_job_spec_carries_the_actor(tmp_path: Path) -> None:
    # The actor travels so the worker can attribute the recorded run to the user (BE-0015).
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.new_job(["run"], actor="alice", org="acme")
    spec = job_spec(job)
    assert spec["actor"] == "alice"
    assert spec["org"] == "acme"


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
    # The worker publishes the job's log to the (Redis) LogBus it's given, so a control-plane
    # replica streaming the same job id over its own RedisLogBus replays the log cross-process —
    # the gap W1 closes (the worker no longer logs into a private in-memory bus).
    project(tmp_path)
    redis = _FakeRedis()
    spec = {"job_id": "7", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    execute_job_spec(
        spec,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
        cwd=tmp_path,
        bus=RedisLogBus(redis),
    )
    # A separate bus over the same Redis (a different process) replays the worker's log, then ends
    # (run_job closes the channel on exit, so the stream is finite).
    replay = list(RedisLogBus(redis).stream("7"))
    assert "step 0 ok" in replay
    assert any("20260610-1" in line for line in replay)


def test_redis_url_prefers_bajutsu_then_redis_then_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_job, "_broker_url", None
    )  # no in-process override; restored at teardown
    monkeypatch.delenv("BAJUTSU_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert worker_job.redis_url() == "redis://localhost:6379"
    monkeypatch.setenv("REDIS_URL", "redis://r:6379")
    assert worker_job.redis_url() == "redis://r:6379"
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://b:6379")
    assert worker_job.redis_url() == "redis://b:6379"  # BAJUTSU_REDIS_URL wins


def test_set_broker_url_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # The worker records the broker URL in-process (not the environment), so it must take
    # precedence over env vars and never need exporting — credentials stay out of spawned runs.
    monkeypatch.setattr(worker_job, "_broker_url", None)  # restored at teardown
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://env:6379")
    worker_job.set_broker_url("redis://broker:6379")
    assert worker_job.redis_url() == "redis://broker:6379"


def test_execute_job_spec_records_terminal_status_on_the_bus(tmp_path: Path) -> None:
    # The worker records the finished job's status on the bus (W2), so a control-plane replica
    # reads the real exit/run id cross-process — its own Job stays "running".
    project(tmp_path)
    redis = _FakeRedis()
    spec = {"job_id": "9", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    execute_job_spec(
        spec,
        popen=fake_popen(["PASS  runs/20260610-1/manifest.json\n"]),
        cwd=tmp_path,
        bus=RedisLogBus(redis),
    )
    final = RedisLogBus(redis).final("9")
    assert final is not None
    view = json.loads(final)
    assert view["status"] == "done" and view["ok"] is True and view["runId"] == "20260610-1"
    assert "lines" not in view  # the log already lives in the bus stream; don't duplicate it


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
            "bajutsu.config.yaml": "apps: {demo: {bundleId: x}}\n",
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

    payload, code = ops.start_run(state, {"scenario": "smoke.yaml", "app": "demo"})
    assert code == 200 and "jobId" in payload

    spec = q.enqueued[0][1][0]
    cmd = spec["cmd"]
    assert "scenarios/smoke.yaml" in cmd  # workspace-relative --scenario
    assert "bajutsu.config.yaml" in cmd  # workspace-relative --config
    assert "baselines" in cmd  # workspace-relative --baselines (materialized on the worker)
    assert spec["materialize_baselines"] is True
    assert spec["materials"]["scenarios/smoke.yaml"] == scenario_text
    assert "apps:" in spec["materials"]["bajutsu.config.yaml"]


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


class _FakeObjectStore:
    """In-memory ObjectStore (put_bytes) for the worker upload tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get_bytes(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def put_file(self, key: str, path: Path) -> None:
        self.objects[key] = path.read_bytes()

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]


def test_upload_runs_puts_every_file_under_the_artifact_prefix(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "20260610-1"
    (run / "sub").mkdir(parents=True)
    (run / "report.html").write_text("<html>", encoding="utf-8")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    (run / "sub" / "shot.png").write_bytes(b"\x89PNG")
    # Another run in the shared workspace must NOT be uploaded (scoped to run_id).
    other = tmp_path / "runs" / "20260101-0"
    other.mkdir(parents=True)
    (other / "report.html").write_text("old", encoding="utf-8")
    # A symlink in the run dir must be skipped (no exfiltration outside the run tree).
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (run / "link.txt").symlink_to(secret)

    store = _FakeObjectStore()
    worker_job._upload_runs(tmp_path, store, "artifacts/", "20260610-1")
    assert store.objects["artifacts/20260610-1/report.html"] == b"<html>"
    assert store.objects["artifacts/20260610-1/manifest.json"] == b"{}"
    assert store.objects["artifacts/20260610-1/sub/shot.png"] == b"\x89PNG"
    assert "artifacts/20260101-0/report.html" not in store.objects  # other run not uploaded
    assert "artifacts/20260610-1/link.txt" not in store.objects  # symlink skipped


def test_execute_job_spec_uploads_the_run_tree(tmp_path: Path) -> None:
    # The worker uploads the run tree the subprocess wrote, to the keys the artifact store serves.
    def popen_writing_run(_cmd: list[str], **_kw: object) -> FakeProc:
        run = tmp_path / "runs" / "20260610-1"
        run.mkdir(parents=True, exist_ok=True)
        (run / "report.html").write_text("<html>", encoding="utf-8")
        return FakeProc(["PASS  runs/20260610-1/manifest.json\n"])

    store = _FakeObjectStore()
    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    execute_job_spec(
        spec, popen=popen_writing_run, cwd=tmp_path, bus=srv.InMemoryLogBus(), store=store
    )
    assert store.objects["artifacts/20260610-1/report.html"] == b"<html>"


def test_execute_job_spec_uploads_under_the_orgs_prefix(tmp_path: Path) -> None:
    # A non-default org's run tree uploads under the org segment, matching the control plane's
    # org-scoped artifact store (BE-0015 multi-tenancy).
    def popen_writing_run(_cmd: list[str], **_kw: object) -> FakeProc:
        run = tmp_path / "runs" / "20260610-1"
        run.mkdir(parents=True, exist_ok=True)
        (run / "report.html").write_text("<html>", encoding="utf-8")
        return FakeProc(["PASS  runs/20260610-1/manifest.json\n"])

    store = _FakeObjectStore()
    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "run"],
        "udids": [],
        "app_path": None,
        "build": None,
        "org": "acme",
    }
    execute_job_spec(
        spec, popen=popen_writing_run, cwd=tmp_path, bus=srv.InMemoryLogBus(), store=store
    )
    assert store.objects["acme/artifacts/20260610-1/report.html"] == b"<html>"


def test_execute_job_spec_skips_upload_without_a_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No object store configured (no BAJUTSU_S3_BUCKET) -> a finished run isn't failed; it just
    # doesn't upload.
    monkeypatch.delenv("BAJUTSU_S3_BUCKET", raising=False)
    project(tmp_path)
    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    job = execute_job_spec(spec, popen=fake_popen(["ok\n"]), cwd=tmp_path, bus=srv.InMemoryLogBus())
    assert job.view()["status"] == "done"  # ran fine, just no upload


def test_execute_job_spec_saves_the_authored_scenario(tmp_path: Path) -> None:
    # A server-backend `record`: the worker writes the authored scenario to its workspace, then
    # persists it to the same object-storage key the control plane reads (scenarios/<app>/<ref>).
    def popen_writing_scenario(_cmd: list[str], **_kw: object) -> FakeProc:
        out = tmp_path / "scenarios" / "login.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("- name: login\n  steps: []\n", encoding="utf-8")
        return FakeProc(["authored ok\n"])

    store = _FakeObjectStore()
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
        spec, popen=popen_writing_scenario, cwd=tmp_path, bus=srv.InMemoryLogBus(), store=store
    )
    assert store.objects["scenarios/demo/login.yaml"] == b"- name: login\n  steps: []\n"
    assert job.view()["outPath"] == "scenarios/login.yaml"  # terminal status reports the out path


def test_execute_job_spec_save_authored_confines_to_the_workspace(tmp_path: Path) -> None:
    # A crafted spec with an escaping out_path must not read & upload a host file outside the
    # workspace (defensive — the control plane never builds such a path).
    secret = tmp_path / "secret.yaml"
    secret.write_text("secret", encoding="utf-8")
    store = _FakeObjectStore()
    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "record"],
        "udids": [],
        "app_path": None,
        "build": None,
        "out_path": "../secret.yaml",
        "record_save": ["demo", "stolen.yaml"],
    }
    execute_job_spec(
        spec, popen=fake_popen(["ok\n"]), cwd=tmp_path / "ws", bus=srv.InMemoryLogBus(), store=store
    )
    assert store.objects == {}  # nothing read/uploaded from outside the workspace


def test_start_record_on_the_server_backend_materializes_and_targets_storage(
    tmp_path: Path,
) -> None:
    from bajutsu.serve.server.scenarios import StorageScenarioStore

    _scn, cfg, runs = project(tmp_path)
    q = _FakeQueue()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path, executor=QueueExecutor(q))
    state.scenarios = StorageScenarioStore(FakeScenarioStorage({"demo": {}}))

    payload, code = ops.start_record(state, {"app": "demo", "goal": "log in", "name": "login"})
    assert code == 200 and payload["path"] == "login.yaml"

    spec = q.enqueued[0][1][0]
    assert "scenarios/login.yaml" in spec["cmd"]  # workspace-relative --out
    assert "bajutsu.config.yaml" in spec["cmd"]  # config materialized
    assert spec["record_save"] == ["demo", "login.yaml"]
    assert "apps:" in spec["materials"]["bajutsu.config.yaml"]


def test_execute_job_spec_materializes_baselines(tmp_path: Path) -> None:
    # A server-backend run downloads the visual baselines into work/baselines before running, so the
    # cmd's `--baselines baselines` resolves on the worker.
    store = _FakeObjectStore()
    store.objects["baselines/home.png"] = b"\x89PNG"
    spec = {
        "job_id": "1",
        "cmd": ["bajutsu", "run"],
        "udids": [],
        "app_path": None,
        "build": None,
        "materialize_baselines": True,
    }
    # A stale baseline from a previous job in the reused workspace must be cleared before download.
    (tmp_path / "baselines").mkdir()
    (tmp_path / "baselines" / "stale.png").write_bytes(b"old")
    execute_job_spec(
        spec, popen=fake_popen(["ok\n"]), cwd=tmp_path, bus=srv.InMemoryLogBus(), store=store
    )
    assert (tmp_path / "baselines" / "home.png").read_bytes() == b"\x89PNG"
    assert not (tmp_path / "baselines" / "stale.png").exists()  # cleared before re-materialize

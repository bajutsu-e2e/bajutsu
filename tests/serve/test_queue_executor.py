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
from _shared import fake_popen, project

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
    }
    json.dumps(spec)  # must carry no live objects (locks/Popen/bus) — JSON round-trips


def test_job_spec_round_trips_through_json(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    job = state.new_job(["run"], udids=["A", "B"], app_path=None, build=None)
    spec = job_spec(job)
    assert json.loads(json.dumps(spec)) == spec


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


def testredis_url_prefers_bajutsu_then_redis_then_default(
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

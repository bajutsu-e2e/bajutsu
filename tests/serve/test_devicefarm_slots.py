"""A Device Farm worker runs concurrent slots under its declared budget (BE-0448 units 4, 5, and 8).

The fan-out is driven end to end: `start_run_set` queues one job per scenario in the database, and
the worker's slot loop leases them over the real worker operations, submits each to an in-memory
Device Farm fake, and posts the verdicts back. The budget is the worker file's `maxJobConcurrency`;
a shutdown lets in-flight leases lapse, and a restarted worker resumes each run from its stored ARN.
The server-side budget settings that capped the fan-out for one release are gone (BE-0452).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common import deprecations
from bajutsu.serve import batch_provider as bp
from bajutsu.serve import operations as ops
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.logbus import InMemoryLogBus
from bajutsu.serve.operations.dispatch import start_run_set
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base
from bajutsu.serve.server.worker_job import execute_job_spec
from bajutsu.serve.state import Job, ServeState

runner = CliRunner()

_SCENARIO = "- name: {name}\n  steps:\n    - tap: {{ id: x }}\n"
_TWO_TARGETS = (
    "- name: pair\n  targets: [demo, other]\n  steps:\n    - tap: { id: x }\n      target: demo\n"
)
_WAIT = 10.0


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    saved = dict(bp._PROVIDERS)
    try:
        yield
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)


@pytest.fixture(autouse=True)
def _fresh_notices() -> Iterator[None]:
    saved = set(deprecations._emitted)
    deprecations._emitted.clear()
    try:
        yield
    finally:
        deprecations._emitted.clear()
        deprecations._emitted.update(saved)


class _Cloud:
    """A thread-safe Device Farm fake: each run stays RUNNING until the test finishes it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.scheduled: list[str] = []  # the job id each run was named after
        self.stopped: list[str] = []
        self._finished: set[str] = set()

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        return {"upload": {"arn": f"arn:upload/{name}", "url": f"https://s3/{name}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        return {"upload": {"arn": arn, "status": "SUCCEEDED"}}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.scheduled.append(kwargs["name"])
            return {"run": {"arn": f"arn:run/{kwargs['name']}"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        with self._lock:
            done = arn in self._finished
        return {"run": {"arn": arn, "status": "COMPLETED" if done else "RUNNING"}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {"artifacts": [{"name": "runs", "extension": "zip", "url": f"https://s3/{arn}"}]}

    def stop_run(self, *, arn: str) -> dict[str, Any]:
        with self._lock:
            self.stopped.append(arn)
        return {"run": {"arn": arn, "status": "STOPPING"}}

    def finish(self, job_id: str) -> None:
        with self._lock:
            self._finished.add(f"arn:run/{job_id}")


class _Transfer:
    """Hands each run its own run tree, keyed by the run's ARN, so landed runs never collide."""

    def upload(self, url: str, path: Path) -> None:
        pass

    def download(self, url: str) -> bytes:
        import io
        import json
        import zipfile

        run_id = "20260101-" + url.rsplit("/", 1)[-1]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            manifest = {"ok": True, "scenarios": [{"scenario": "a", "ok": True}]}
            zf.writestr(f"runs/{run_id}/manifest.json", json.dumps(manifest))
        return buffer.getvalue()


def _register(cloud: _Cloud) -> None:
    bp.register(
        "devicefarm",
        bp.DeviceFarmBatchProvider(
            client=cloud,
            transfer=_Transfer(),
            project_arn="arn:project/1",
            # A short real pause, so the slots' polls do not spin while their runs are RUNNING.
            sleep=lambda _s: time.sleep(0.01),
        ),
    )


def _fan_out_state(tmp_path: Path, scenarios: dict[str, str]) -> ServeState:
    """A database-backed server whose `demo` target fans out to Device Farm from its own directory."""
    project = tmp_path / "project"
    (project / "scenarios").mkdir(parents=True)
    for name, text in scenarios.items():
        (project / "scenarios" / name).write_text(text, encoding="utf-8")
    (project / "app.apk").write_bytes(b"APK")
    config = project / "bajutsu.config.yaml"
    config.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            "    app: {id: com.example.demo, path: app.apk}\n"
            "    paths: {scenarios: scenarios}\n"
        ),
        encoding="utf-8",
    )
    # A file, not the in-memory database: every slot thread opens its own connection.
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    return ServeState(
        config=config,
        runs_dir=tmp_path / "runs",
        cwd=project,
        executor=DbQueueExecutor(repo),
        repository=repo,
    )


def _worker_file(tmp_path: Path, *, slots: int, extra: str = "") -> Path:
    path = tmp_path / "devicefarm.worker.yaml"
    path.write_text(
        f"version: 1\nenvironment: devicefarm\nmaxJobConcurrency: {slots}\n"
        f"drivers: [adb, xcuitest]\n{extra}",
        encoding="utf-8",
    )
    return path


class _Server:
    """Stands in for `_post_json`, answering each worker route from the real operations."""

    def __init__(self, state: ServeState) -> None:
        self._state = state
        self._lock = threading.Lock()
        self.calls: list[str] = []

    def count(self, path: str) -> int:
        with self._lock:
            return self.calls.count(path)

    def __call__(
        self,
        url: str,
        body: dict[str, Any],
        *,
        token: str | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        path = url.removeprefix("http://server")
        with self._lock:
            self.calls.append(path)
        state = self._state
        if path == "/api/worker/lease":
            payload, code = ops.worker_lease(state, body["worker_id"], body.get("capabilities"))
        elif path == "/api/worker/heartbeat":
            payload, code = ops.worker_heartbeat(state, body["worker_id"], body["job_id"])
        elif path == "/api/worker/batch-run":
            payload, code = ops.worker_batch_run(state, body)
        elif path == "/api/worker/result":
            payload, code = ops.worker_result(state, body)
        else:  # the log route, and evidence uploads with no store configured: nothing to sign
            payload, code = {"urls": {}}, 200
        return code, payload


def _start_worker(
    server: _Server,
    tmp_path: Path,
    *,
    wid: str,
    slots: int,
    stopped: Callable[[], bool],
    worker_file: Path,
) -> tuple[threading.Thread, worker_mod._SlotShutdown]:
    shutdown = worker_mod._SlotShutdown()
    thread = threading.Thread(
        target=worker_mod._serve_slots,
        kwargs={
            "url": "http://server",
            "auth_token": None,
            "wid": wid,
            "caps": ["environment:devicefarm"],
            "work": tmp_path / wid,
            "poll_interval": 0.01,
            "heartbeat_interval": 0.05,
            "worker_file": worker_file,
            "slots": slots,
            "shutdown": shutdown,
            "stopped": stopped,
            "environment": "devicefarm",
        },
        daemon=True,
    )
    thread.start()
    return thread, shutdown


def _until(condition: Callable[[], bool]) -> None:
    give_up = time.monotonic() + _WAIT
    while not condition():
        assert time.monotonic() < give_up, "timed out"
        time.sleep(0.01)


def _status(state: ServeState, job_id: str) -> dict[str, Any]:
    assert state.repository is not None
    info = state.repository.get_job(job_id)
    assert info is not None
    return dict(info)


# --- unit 4: concurrent slots under maxJobConcurrency --------------------------------------------


def test_a_full_worker_sends_no_lease_request_until_a_result_is_posted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ("one.yaml", "two.yaml", "three.yaml")
    state = _fan_out_state(tmp_path, {n: _SCENARIO.format(name=n[:-5]) for n in names})
    payload, code = start_run_set(state, {"target": "demo", "environment": "devicefarm"})
    assert code == 200 and "warnings" not in payload
    first, second, third = payload["jobIds"]
    cloud = _Cloud()
    _register(cloud)
    server = _Server(state)
    monkeypatch.setattr(worker_mod, "_post_json", server)
    # Hold the first job's evidence upload until the third job is scheduled: the first job has then
    # freed its place but not its workspace, so the third must get a workspace of its own.
    upload = worker_mod._upload_evidence

    def held_upload(job_work: Path, run_id: str, **kwargs: Any) -> None:
        if run_id == f"20260101-{first}":
            _until(lambda: len(cloud.scheduled) == 3)
        upload(job_work, run_id, **kwargs)

    monkeypatch.setattr(worker_mod, "_upload_evidence", held_upload)

    thread, _shutdown = _start_worker(
        server,
        tmp_path,
        wid="w1",
        slots=2,
        stopped=lambda: server.count("/api/worker/result") >= 3,
        worker_file=_worker_file(tmp_path, slots=2),
    )
    _until(lambda: len(cloud.scheduled) == 2)
    time.sleep(0.2)  # many poll intervals: a worker that kept asking would have leased the third

    assert sorted(cloud.scheduled) == sorted([first, second])  # two runs at once, one per slot
    assert server.count("/api/worker/lease") == 2
    assert _status(state, third)["status"] == "queued"

    cloud.finish(first)
    _until(lambda: len(cloud.scheduled) == 3)
    cloud.finish(second)
    cloud.finish(third)
    thread.join(timeout=_WAIT)

    assert not thread.is_alive()
    for job_id in (first, second, third):
        info = _status(state, job_id)
        assert info["status"] == "done" and info["result"]["ok"] is True
        assert info["result"]["runId"] == f"20260101-{job_id}"
    assert cloud.stopped == []
    # The third job never shared the tree the first job was still uploading from.
    assert sorted(p.name for p in (tmp_path / "w1" / ".slots").iterdir()) == ["0", "1", "2"]


def test_a_shutdown_lets_leases_lapse_and_a_restarted_worker_resumes_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The mid-poll restart, through the queue: the first worker stops while both runs are polling,
    # stops neither run and posts nothing, and the worker that leases the jobs next resumes each run
    # from the ARN the server stored, without a second submission.
    state = _fan_out_state(
        tmp_path, {n: _SCENARIO.format(name=n[:-5]) for n in ("one.yaml", "two.yaml")}
    )
    payload, _code = start_run_set(state, {"target": "demo", "environment": "devicefarm"})
    job_ids = payload["jobIds"]
    cloud = _Cloud()
    _register(cloud)
    server = _Server(state)
    monkeypatch.setattr(worker_mod, "_post_json", server)
    worker_file = _worker_file(tmp_path, slots=2)

    first, shutdown = _start_worker(
        server, tmp_path, wid="w1", slots=2, stopped=lambda: False, worker_file=worker_file
    )
    _until(lambda: server.count("/api/worker/batch-run") == 2)
    shutdown.request()  # what a SIGTERM to the worker does
    first.join(timeout=_WAIT)

    assert not first.is_alive()
    assert cloud.stopped == [] and server.count("/api/worker/result") == 0
    assert state.repository is not None
    assert sorted(state.repository.reclaim_expired_leases(timedelta(0))) == sorted(job_ids)

    for job_id in job_ids:
        cloud.finish(job_id)
    second, _ = _start_worker(
        server,
        tmp_path,
        wid="w2",
        slots=2,
        stopped=lambda: server.count("/api/worker/result") >= 2,
        worker_file=worker_file,
    )
    second.join(timeout=_WAIT)

    assert not second.is_alive()
    assert sorted(cloud.scheduled) == sorted(job_ids)  # one submission per job, ever
    for job_id in job_ids:
        assert _status(state, job_id)["result"]["ok"] is True


def test_a_shutdown_stops_a_run_whose_arn_the_server_does_not_hold(tmp_path: Path) -> None:
    # The stop lands between scheduling the run and storing its ARN. Nobody could resume such a run,
    # so leaving it would only hold a device until Device Farm ends it: the slot stops it instead.
    shutdown = worker_mod._SlotShutdown()

    class _StopAtSchedule(_Cloud):
        def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
            scheduled = super().schedule_run(**kwargs)
            shutdown.request()
            return scheduled

    cloud = _StopAtSchedule()
    _register(cloud)
    project = tmp_path / "project"
    (project / "scenarios").mkdir(parents=True)
    (project / "scenarios" / "one.yaml").write_text(_SCENARIO.format(name="a"), encoding="utf-8")
    (project / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    (project / "app.apk").write_bytes(b"APK")
    spec = {
        "job_id": "j1",
        "cmd": [],
        "cwd": str(project),
        "batch": {
            "provider": "devicefarm",
            "scenario": "scenarios/one.yaml",
            "target": "demo",
            "config": "bajutsu.config.yaml",
            "platform": "android",
            "app_path": "app.apk",
        },
    }
    lost = threading.Event()
    shutdown.attach(lost)
    checkpoint = worker_mod._LeaseCheckpoint(
        url="http://server", auth_token=None, wid="w1", job_id="j1", stored=None, lost=lost
    )

    job = execute_job_spec(
        spec, cwd=project, bus=InMemoryLogBus(), batch_checkpoint=checkpoint, lease_lost=lost
    )

    assert lost.is_set() and cloud.stopped == ["arn:run/j1"]
    assert job.view()["ok"] is False


def test_slot_workspaces_are_never_shared_while_a_job_holds_one(tmp_path: Path) -> None:
    dirs = worker_mod._SlotDirs(tmp_path)
    first, second = dirs.acquire(), dirs.acquire()
    assert first != second and first.is_dir() and second.is_dir()
    dirs.release(first)
    assert dirs.acquire() == first  # reused once free, so its bundle cache is reused too
    assert dirs.acquire() == tmp_path / ".slots" / "2"


def test_a_second_stop_ends_the_wait_for_the_slots() -> None:
    shutdown = worker_mod._SlotShutdown()
    lost = threading.Event()
    shutdown.attach(lost)
    assert shutdown.pgid() is None and not shutdown.stopping
    shutdown.request()
    assert shutdown.stopping and lost.is_set()
    late = threading.Event()
    shutdown.attach(late)  # a slot that started as the stop arrived lapses at once
    assert late.is_set()
    shutdown.detach(late)
    assert not shutdown.now
    shutdown.kill()
    assert shutdown.now


def test_a_slot_that_fails_frees_its_place(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(worker_mod, "_run_with_heartbeat", boom)
    freed: list[bool] = []
    dirs = worker_mod._SlotDirs(tmp_path)
    worker_mod._run_slot(
        body={"job_id": "j1", "spec": {}},
        dirs=dirs,
        free=lambda: freed.append(True),
        shutdown=worker_mod._SlotShutdown(),
        url="http://server",
        auth_token=None,
        wid="w1",
        heartbeat_interval=0.05,
        worker_file=None,
        environment="devicefarm",
    )
    assert freed == [True]
    assert dirs.acquire() == tmp_path / ".slots" / "0"


def test_a_slot_whose_run_raises_leaves_no_batch_tree_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The job's tree holds the uploaded binary; the reused slot directory must not keep it.
    def boom(*_a: Any, work: Path, **_k: Any) -> Any:
        tree = work / ".batch" / "j1"
        tree.mkdir(parents=True)
        (tree / "app.apk").write_bytes(b"APK")
        raise RuntimeError("unexpected")

    monkeypatch.setattr(worker_mod, "_run_with_heartbeat", boom)
    dirs = worker_mod._SlotDirs(tmp_path)
    worker_mod._run_slot(
        body={"job_id": "j1", "spec": {}},
        dirs=dirs,
        free=lambda: None,
        shutdown=worker_mod._SlotShutdown(),
        url="http://server",
        auth_token=None,
        wid="w1",
        heartbeat_interval=0.05,
        worker_file=None,
        environment="devicefarm",
    )
    assert not (tmp_path / ".slots" / "0" / ".batch").exists()


def test_a_runner_already_done_under_a_shutdown_leaves_the_lease_to_lapse() -> None:
    runner = threading.Thread(target=lambda: None)
    runner.start()
    runner.join()
    lost = threading.Event()
    lost.set()
    assert worker_mod._beat_until_done(
        runner, beat=lambda: 200, interval=0.01, lost=lost, streamer=None
    )


def test_a_malformed_lease_frees_its_place_and_takes_no_workspace(tmp_path: Path) -> None:
    freed: list[bool] = []
    dirs = worker_mod._SlotDirs(tmp_path)
    worker_mod._run_slot(
        body={"job_id": "j1"},  # no spec
        dirs=dirs,
        free=lambda: freed.append(True),
        shutdown=worker_mod._SlotShutdown(),
        url="http://server",
        auth_token=None,
        wid="w1",
        heartbeat_interval=0.05,
        worker_file=None,
        environment="devicefarm",
    )
    assert freed == [True]
    assert not (tmp_path / ".slots").exists()


def test_a_failing_lease_loop_still_lets_in_flight_leases_lapse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown = worker_mod._SlotShutdown()
    lost = threading.Event()
    shutdown.attach(lost)  # a slot already in flight

    def boom(*_a: Any) -> Any:
        raise RuntimeError("lease exploded")

    monkeypatch.setattr(worker_mod, "_lease", boom)
    with pytest.raises(RuntimeError, match="lease exploded"):
        worker_mod._serve_slots(
            url="http://server",
            auth_token=None,
            wid="w1",
            caps=["environment:devicefarm"],
            work=tmp_path,
            poll_interval=0.01,
            heartbeat_interval=0.05,
            worker_file=None,
            slots=2,
            shutdown=shutdown,
            stopped=lambda: False,
            environment="devicefarm",
        )
    assert shutdown.stopping and lost.is_set()


def test_a_slot_whose_thread_cannot_start_gives_its_place_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leases = iter([{"job_id": "j1", "spec": {}}, None])
    monkeypatch.setattr(worker_mod, "_lease", lambda *_a: next(leases))

    class _NoStart(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("cannot start a thread")

    monkeypatch.setattr(threading, "Thread", _NoStart)
    shutdown = worker_mod._SlotShutdown()
    with pytest.raises(RuntimeError, match="cannot start"):
        worker_mod._serve_slots(
            url="http://server",
            auth_token=None,
            wid="w1",
            caps=["environment:devicefarm"],
            work=tmp_path,
            poll_interval=0.01,
            heartbeat_interval=0.05,
            worker_file=None,
            slots=1,
            shutdown=shutdown,
            stopped=lambda: False,
            environment="devicefarm",
        )
    assert shutdown.stopping


def test_a_resumed_run_skips_the_check_so_its_device_is_not_orphaned(tmp_path: Path) -> None:
    # The job's ARN is stored, so an earlier attempt already submitted it. A worker whose drivers
    # would refuse the scenario still polls the run to its verdict instead of failing the job.
    cloud = _Cloud()
    cloud.finish("j1")
    _register(cloud)
    project = tmp_path / "project"
    (project / "scenarios").mkdir(parents=True)
    (project / "scenarios" / "one.yaml").write_text(_SCENARIO.format(name="a"), encoding="utf-8")
    (project / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    (project / "app.apk").write_bytes(b"APK")
    worker_file = _worker_file(tmp_path, slots=1)
    worker_file.write_text(
        worker_file.read_text(encoding="utf-8").replace("[adb, xcuitest]", "[xcuitest]"),
        encoding="utf-8",
    )
    spec = {
        "job_id": "j1",
        "cmd": [],
        "cwd": str(project),
        "batch": {
            "provider": "devicefarm",
            "scenario": "scenarios/one.yaml",
            "target": "demo",
            "config": "bajutsu.config.yaml",
            "platform": "android",
            "app_path": "app.apk",
        },
    }

    def run(stored: str | None) -> Job:
        lost = threading.Event()
        checkpoint = worker_mod._LeaseCheckpoint(
            url="http://server", auth_token=None, wid="w1", job_id="j1", stored=stored, lost=lost
        )
        return execute_job_spec(
            spec,
            cwd=project,
            bus=InMemoryLogBus(),
            worker_config=worker_file,
            batch_checkpoint=checkpoint,
            lease_lost=lost,
        )

    assert "worker-capability-unsupported:" in str(run(None).view()["error"])
    assert cloud.scheduled == []
    resumed = run("arn:run/j1")
    assert resumed.view()["ok"] is True and cloud.scheduled == []


@pytest.mark.parametrize(("slots", "loop"), [(2, "_serve_slots"), (1, "_serve_leases")])
def test_the_worker_picks_its_loop_from_max_job_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slots: int, loop: str
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(worker_mod, "_register_environment_or_exit", lambda _e: None)
    for name in ("_serve_slots", "_serve_leases"):
        monkeypatch.setattr(
            worker_mod, name, lambda _n=name, **kw: seen.update(loop=_n, slots=kw.get("slots"))
        )
    worker_file = _worker_file(tmp_path, slots=slots)

    result = runner.invoke(
        app, ["worker", "--worker-config", str(worker_file), "--workdir", str(tmp_path / "w")]
    )

    assert result.exit_code == 0, result.output
    assert seen["loop"] == loop
    assert ("job slots: 2" in result.output) is (slots == 2)
    if slots == 2:
        assert seen["slots"] == 2


# --- unit 5: the worker capability check -----------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "drivers", "scenario", "words"),
    [
        ("maxTargetsPerJob: 1\n", None, _TWO_TARGETS, "declares 2 targets"),
        ("", "[xcuitest]", _SCENARIO.format(name="a"), "adb is not among this worker's drivers"),
    ],
)
def test_a_scenario_the_worker_cannot_run_fails_before_anything_is_submitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: str,
    drivers: str | None,
    scenario: str,
    words: str,
) -> None:
    state = _fan_out_state(tmp_path, {"one.yaml": scenario})
    payload, _code = start_run_set(state, {"target": "demo", "environment": "devicefarm"})
    (job_id,) = payload["jobIds"]
    cloud = _Cloud()
    _register(cloud)
    server = _Server(state)
    monkeypatch.setattr(worker_mod, "_post_json", server)
    worker_file = _worker_file(tmp_path, slots=2, extra=extra)
    if drivers is not None:
        text = worker_file.read_text(encoding="utf-8")
        worker_file.write_text(text.replace("[adb, xcuitest]", drivers), encoding="utf-8")

    thread, _ = _start_worker(
        server,
        tmp_path,
        wid="w1",
        slots=2,
        stopped=lambda: server.count("/api/worker/result") >= 1,
        worker_file=worker_file,
    )
    thread.join(timeout=_WAIT)

    info = _status(state, job_id)
    assert info["status"] == "failed"
    assert "worker-capability-unsupported:" in info["result"]["error"]
    assert words in info["result"]["error"]
    assert cloud.scheduled == []


def test_an_unreadable_scenario_fails_the_check(tmp_path: Path) -> None:
    from bajutsu.common.capability.worker_capability import load_worker_capability
    from bajutsu.serve.jobs import _batch_capability_reason

    worker = load_worker_capability(_worker_file(tmp_path, slots=1))
    request = bp.BatchRequest(
        provider="devicefarm",
        scenario="scenarios/missing.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path="app.apk",
    )

    reason = _batch_capability_reason(request, tmp_path, worker)

    assert reason is not None and "cannot read the scenario" in reason


def test_a_local_worker_file_checks_no_batch_job(tmp_path: Path) -> None:
    from bajutsu.serve.server.worker_job import _batch_worker_capability

    local = tmp_path / "local.yaml"
    local.write_text("version: 1\nenvironment: local\ndrivers: [adb]\n", encoding="utf-8")
    assert _batch_worker_capability({"batch": {}}, local) is None
    assert _batch_worker_capability({"batch": {}}, None) is None
    assert _batch_worker_capability({}, _worker_file(tmp_path, slots=1)) is None
    checked = _batch_worker_capability({"batch": {}}, _worker_file(tmp_path, slots=1))
    assert checked is not None and checked.environment == "devicefarm"

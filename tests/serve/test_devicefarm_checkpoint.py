"""A Device Farm worker stores its run over HTTP, stops it on a cancel, and posts its result (BE-0448).

Units 3, 6, and 7: a job that ships materials gets its application through the lease's signed URL;
the scheduled run's ARN is stored through the server's lease-owner-checked route, so a re-leased job
resumes it instead of submitting again; a cancel or a stop signal stops the run through `stop_run`;
and the worker posts the result and uploads the landed run through the ordinary worker path.
Everything runs against an in-memory Device Farm fake and the real worker operations over SQLite.
"""

from __future__ import annotations

import io
import json
import os
import signal
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
from conftest import FakeObjectStore
from sqlalchemy import create_engine
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common.cloud.devicefarm import RunInterrupted, submit_and_collect
from bajutsu.serve import batch_bootstrap
from bajutsu.serve import batch_provider as bp
from bajutsu.serve import operations as ops
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.logbus import InMemoryLogBus
from bajutsu.serve.server import one_shot
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base
from bajutsu.serve.server.worker_job import CancelRelay, execute_job_spec
from bajutsu.serve.state import ServeState

runner = CliRunner()

_SCENARIO = "- name: a\n  steps:\n    - tap: { id: x }\n"
_SHA = "b" * 64


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    saved = dict(bp._PROVIDERS)
    try:
        yield
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)


def _zip_bytes(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


# A poll count no test reaches when the run is stopped as intended: a fake run that is never stopped
# fails the test instead of hanging the suite (a self-sent SIGINT was seen to go missing under xdist).
_MAX_POLLS = 2000


class _Client:
    """A Device Farm fake whose run stays RUNNING until `complete` is set; *on_poll* sees each poll."""

    def __init__(self, on_poll: Callable[[int], None] | None = None) -> None:
        self.scheduled: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.polls = 0
        self.complete = threading.Event()
        self._on_poll = on_poll

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        return {"upload": {"arn": f"arn:upload/{name}", "url": f"https://s3/{name}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        return {"upload": {"arn": arn, "status": "SUCCEEDED"}}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        self.scheduled.append(kwargs)
        return {"run": {"arn": f"arn:run/{len(self.scheduled)}"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        self.polls += 1
        if self.polls > _MAX_POLLS:
            raise AssertionError(
                f"the fake run was polled {self.polls} times without being stopped"
            )
        if self._on_poll is not None:
            self._on_poll(self.polls)
        status = "COMPLETED" if self.complete.is_set() else "RUNNING"
        return {"run": {"arn": arn, "status": status}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {"artifacts": [{"name": "runs", "extension": "zip", "url": "https://s3/runs.zip"}]}

    def stop_run(self, *, arn: str) -> dict[str, Any]:
        self.stopped.append(arn)
        return {"run": {"arn": arn, "status": "STOPPING"}}


class _Transfer:
    def upload(self, url: str, path: Path) -> None:
        pass

    def download(self, url: str) -> bytes:
        manifest = json.dumps({"ok": True, "scenarios": [{"scenario": "a", "ok": True}]})
        return _zip_bytes({"runs/20260101-1/manifest.json": manifest})


def _register(client: _Client, *, sleep: Callable[[float], None] = lambda _: None) -> None:
    bp.register(
        "devicefarm",
        bp.DeviceFarmBatchProvider(
            client=client, transfer=_Transfer(), project_arn="arn:project/1", sleep=sleep
        ),
    )


def _project(root: Path) -> Path:
    (root / "scenarios").mkdir(parents=True)
    (root / "scenarios" / "one.yaml").write_text(_SCENARIO, encoding="utf-8")
    (root / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    (root / "app.apk").write_bytes(b"APK")
    return root


def _batch() -> dict[str, str]:
    return asdict(
        bp.BatchRequest(
            provider="devicefarm",
            scenario="scenarios/one.yaml",
            target="demo",
            config="bajutsu.config.yaml",
            platform="android",
            app_path="app.apk",
        )
    )


def _host_spec(job_id: str, project: Path) -> dict[str, Any]:
    """A job naming a directory on this machine, so no fetch stands between lease and submit."""
    return {"job_id": job_id, "cmd": [], "cwd": str(project), "batch": _batch()}


def _db_state(tmp_path: Path) -> tuple[ServeState, Any]:
    # A file, not the in-memory database: the run thread and the heartbeat each open their own
    # connection, and an in-memory SQLite database exists for one connection alone.
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = ServeState(runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo)
    return state, repo


def _route(state: ServeState, calls: list[tuple[str, dict[str, Any]]]) -> Any:
    """Stand in for `_post_json`, answering each worker route from the real operations."""

    def post(
        url: str, body: dict[str, Any], *, token: str | None = None, timeout: float | None = None
    ) -> tuple[int, Any]:
        path = url.removeprefix("http://server")
        calls.append((path, body))
        if path == "/api/worker/lease":
            payload, code = ops.worker_lease(state, body["worker_id"], body.get("capabilities"))
        elif path == "/api/worker/heartbeat":
            payload, code = ops.worker_heartbeat(state, body["worker_id"], body["job_id"])
        elif path == "/api/worker/batch-run":
            payload, code = ops.worker_batch_run(state, body)
        elif path == "/api/worker/result":
            payload, code = ops.worker_result(state, body)
        elif path == "/api/worker/artifact-urls":
            payload, code = ops.worker_artifact_urls(state, body)
        else:  # the evidence store is not configured, so there is nothing to sign
            payload, code = {"urls": {}}, 200
        return code, payload

    return post


def _checkpoint(job_id: str, wid: str, stored: str | None, lost: threading.Event) -> Any:
    return worker_mod._LeaseCheckpoint(
        url="http://server", auth_token=None, wid=wid, job_id=job_id, stored=stored, lost=lost
    )


# --- unit 3: the application reaches a worker through the lease's signed URL ---------------------


def test_the_lease_signs_the_stored_application(tmp_path: Path) -> None:
    state, repo = _db_state(tmp_path)
    state.object_store = FakeObjectStore()
    repo.enqueue_job("j1", "acme", {"job_id": "j1", "batch": _batch(), "batch_app": _SHA})

    payload, code = ops.worker_lease(state, "w1")

    assert code == 200
    assert payload["app_url"].endswith(f"acme/uploads/binary/{_SHA}")
    assert "batch_run_arn" not in payload  # nothing scheduled yet


def test_a_lease_signs_no_application_for_a_malformed_sha(tmp_path: Path) -> None:
    state, repo = _db_state(tmp_path)
    state.object_store = FakeObjectStore()
    repo.enqueue_job("j1", "acme", {"job_id": "j1", "batch": _batch(), "batch_app": "../x"})

    payload, _code = ops.worker_lease(state, "w1")

    assert "app_url" not in payload


@pytest.mark.parametrize(
    ("app_path", "store", "words"),
    [
        ("app.apk", False, "no object store"),
        ("../app.apk", True, "not under the config's directory"),
        ("/etc/hosts", True, "not under the config's directory"),
        ("missing.apk", True, "names no file"),
    ],
)
def test_a_fan_out_that_cannot_deliver_its_application_is_refused(
    tmp_path: Path, app_path: str, store: bool, words: str
) -> None:
    from test_devicefarm_worker import _Recording, _storage_state

    from bajutsu.serve.operations.dispatch import start_run_set

    executor = _Recording()
    state = _storage_state(tmp_path, executor, app_path=app_path, store=store)
    (tmp_path / "app.apk").write_bytes(b"outside the config's directory")

    payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 400 and words in payload["error"]
    assert executor.jobs == []


def test_the_worker_places_a_verified_application_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    monkeypatch.setattr(worker_mod, "_get_file", lambda _url, dest, **_k: dest.write_bytes(b"APK"))
    tree = tmp_path / "tree"
    tree.mkdir()
    spec: dict[str, Any] = {
        "batch": {**_batch(), "app_path": "build/app.apk"},
        "batch_app": hashlib.sha256(b"APK").hexdigest(),
    }

    worker_mod._place_batch_app(tree, spec, "https://signed/app")

    assert (tree / "build" / "app.apk").read_bytes() == b"APK"


@pytest.mark.parametrize(
    ("app_path", "url", "sha", "words"),
    [
        ("../app.apk", "https://signed/app", _SHA, "outside this job's workspace"),
        ("/tmp/app.apk", "https://signed/app", _SHA, "outside this job's workspace"),
        ("app.apk", None, _SHA, "signed no url"),
        ("app.apk", "https://signed/app", "nope", "invalid application sha"),
    ],
)
def test_the_worker_refuses_an_application_it_cannot_place(
    tmp_path: Path, app_path: str, url: str | None, sha: str, words: str
) -> None:
    spec = {"batch": {**_batch(), "app_path": app_path}, "batch_app": sha}
    with pytest.raises(RuntimeError, match=words):
        worker_mod._place_batch_app(tmp_path, spec, url)
    assert not (tmp_path.parent / "app.apk").exists()


def test_a_corrupted_application_is_left_for_another_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worker_mod, "_get_file", lambda _url, dest, **_k: dest.write_bytes(b"TRUNC")
    )
    spec = {"batch": _batch(), "batch_app": _SHA}
    with pytest.raises(worker_mod._TransientFetch, match="did not match its digest"):
        worker_mod._place_batch_app(tmp_path, spec, "https://signed/app")


def test_a_one_shot_spec_naming_a_stored_application_is_refused() -> None:
    reason = one_shot.refusal({"batch": _batch(), "batch_app": _SHA}, environment="devicefarm")
    assert reason is not None and "stored application" in reason


# --- unit 6: the run's ARN is stored over HTTP, and a re-leased job resumes it --------------------


def test_the_route_stores_the_arn_for_the_lease_holder_alone(tmp_path: Path) -> None:
    state, repo = _db_state(tmp_path)
    repo.enqueue_job("j1", "o", {"job_id": "j1", "batch": _batch()})
    assert repo.lease_job("w1", []) is not None
    stale = {"worker_id": "w2", "job_id": "j1", "run_arn": "arn:run/stale"}

    assert ops.worker_batch_run(state, stale) == (
        {"error": "lease lost or not held by this worker"},
        409,
    )
    assert ops.worker_batch_run(state, {**stale, "run_arn": "x"})[1] == 400
    assert ops.worker_batch_run(state, {**stale, "worker_id": ""})[1] == 400
    assert ops.worker_batch_run(state, {**stale, "job_id": ""})[1] == 400
    assert ops.worker_batch_run(state, {**stale, "worker_id": "w1"})[1] == 200
    assert repo.load_batch_run_arn("j1") == "arn:run/stale"


def test_the_route_needs_a_database(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs")
    body = {"worker_id": "w1", "job_id": "j1", "run_arn": "arn:run/1"}
    assert ops.worker_batch_run(state, body)[1] == 503


def test_the_client_seam_fails_loudly_when_called_directly() -> None:
    # The Protocol's own body raises, so an implementation that forgets `stop_run` cannot pass a
    # silent None off as a stopped run.
    from bajutsu.common.cloud.devicefarm import DeviceFarmClient

    with pytest.raises(NotImplementedError):
        DeviceFarmClient.stop_run(_Client(), arn="arn:run/1")


def test_a_re_leased_job_resumes_the_stored_run_and_never_submits_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first worker schedules the run, stores its ARN, and loses its lease mid-poll; it leaves the
    # run alone. The job is reclaimed, and the worker that leases it next resumes the same run.
    state, repo = _db_state(tmp_path)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(worker_mod, "_post_json", _route(state, calls))
    project = _project(tmp_path / "project")
    repo.enqueue_job("j1", "o", _host_spec("j1", project))
    first_lost = threading.Event()
    client = _Client(on_poll=lambda _n: first_lost.set())
    _register(client)

    lease, _code = ops.worker_lease(state, "w1")
    first = execute_job_spec(
        lease["spec"],
        cwd=project,
        bus=InMemoryLogBus(),
        repository=repo,
        batch_checkpoint=_checkpoint("j1", "w1", lease.get("batch_run_arn"), first_lost),
        lease_lost=first_lost,
    )

    assert "left run arn:run/1" in first.lines[-1]
    assert client.stopped == [] and len(client.scheduled) == 1
    assert client.scheduled[0]["name"] == "j1"  # an operator finds the run by its job id
    assert repo.reclaim_expired_leases(timedelta(0)) == ["j1"]

    lease, _code = ops.worker_lease(state, "w2")
    assert lease["batch_run_arn"] == "arn:run/1"
    client.complete.set()
    second = execute_job_spec(
        lease["spec"],
        cwd=project,
        bus=InMemoryLogBus(),
        repository=repo,
        batch_checkpoint=_checkpoint("j1", "w2", lease["batch_run_arn"], threading.Event()),
        lease_lost=threading.Event(),
    )

    assert second.view()["ok"] is True
    assert len(client.scheduled) == 1  # no second submission, so no second device billed
    assert [p for p, _ in calls] == ["/api/worker/batch-run"]


def test_a_stale_holder_stops_the_run_it_could_not_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lease moved to another worker before this one stored its ARN: nobody could resume the run,
    # so it is stopped, and the ARN the job's holder may store is left untouched.
    state, repo = _db_state(tmp_path)
    monkeypatch.setattr(worker_mod, "_post_json", _route(state, []))
    project = _project(tmp_path / "project")
    repo.enqueue_job("j1", "o", _host_spec("j1", project))
    lease, _code = ops.worker_lease(state, "w1")
    repo.reclaim_expired_leases(timedelta(0))
    assert repo.lease_job("w2", []) is not None
    client = _Client()
    _register(client)

    job = execute_job_spec(
        lease["spec"],
        cwd=project,
        bus=InMemoryLogBus(),
        repository=repo,
        batch_checkpoint=_checkpoint("j1", "w1", None, threading.Event()),
        lease_lost=threading.Event(),
    )

    assert client.stopped == ["arn:run/1"]
    assert job.view()["ok"] is False and "lost before run" in str(job.view()["error"])
    assert repo.load_batch_run_arn("j1") is None


def test_the_save_retries_a_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[Any] = [URLError("reset"), (503, {}), (200, {"ok": True})]

    def post(_url: str, _body: dict[str, Any], **_k: Any) -> tuple[int, Any]:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        code, body = answer
        return code, body

    monkeypatch.setattr(worker_mod, "_post_json", post)
    slept: list[float] = []
    checkpoint = worker_mod._LeaseCheckpoint(
        url="http://server",
        auth_token=None,
        wid="w1",
        job_id="j1",
        stored=None,
        lost=threading.Event(),
        sleep=slept.append,
    )

    checkpoint.save("arn:run/1")

    assert checkpoint.load() == "arn:run/1"
    assert slept == [1.0, 2.0] and answers == []


def test_the_save_gives_up_once_the_lease_is_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    lost = threading.Event()

    def post(_url: str, _body: dict[str, Any], **_k: Any) -> tuple[int, Any]:
        lost.set()  # the heartbeat saw the 409 while this save was failing
        return 503, {}

    monkeypatch.setattr(worker_mod, "_post_json", post)
    checkpoint = _checkpoint("j1", "w1", None, lost)
    checkpoint._sleep = lambda _s: None

    with pytest.raises(
        worker_mod._CheckpointLostError, match="lost before run arn:run/1 was stored"
    ):
        checkpoint.save("arn:run/1")


def _answering(monkeypatch: pytest.MonkeyPatch, *answers: tuple[int, Any]) -> list[int]:
    """Make the route answer *answers* in turn, repeating the last; returns the attempt log."""
    attempts: list[int] = []

    def post(_url: str, _body: dict[str, Any], **_k: Any) -> tuple[int, Any]:
        attempts.append(1)
        code, body = answers[min(len(attempts), len(answers)) - 1]
        return code, body

    monkeypatch.setattr(worker_mod, "_post_json", post)
    return attempts


def test_the_save_gives_up_at_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = _answering(monkeypatch, (503, {}))
    now = [0.0]
    checkpoint = worker_mod._LeaseCheckpoint(
        url="http://server",
        auth_token=None,
        wid="w1",
        job_id="j1",
        stored=None,
        lost=threading.Event(),
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        clock=lambda: now[0],
        deadline=60.0,
    )

    with pytest.raises(worker_mod._CheckpointTimeoutError, match="within 60s"):
        checkpoint.save("arn:run/1")
    assert now[0] <= 60.0 and len(attempts) > 1
    assert checkpoint.load() is None


def test_a_cancel_during_the_retries_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _answering(monkeypatch, (503, {}))
    cancelled = threading.Event()
    checkpoint = worker_mod._LeaseCheckpoint(
        url="http://server",
        auth_token=None,
        wid="w1",
        job_id="j1",
        stored=None,
        lost=threading.Event(),
        cancelled=cancelled.is_set,
        sleep=lambda _s: cancelled.set(),
    )
    client = _Client()

    with pytest.raises(RunInterrupted, match="stopped run arn:run/1"):
        submit_and_collect(
            client,
            _Transfer(),
            project_arn="arn:project/1",
            device_selection={},
            app_path=_project(tmp_path / "p") / "app.apk",
            package_zip=tmp_path / "p" / "app.apk",
            spec_yaml=tmp_path / "p" / "app.apk",
            dest=tmp_path / "dest",
            sleep=lambda _s: None,
            on_scheduled=checkpoint.save,
        )
    assert len(attempts) == 1 and client.stopped == ["arn:run/1"]


@pytest.mark.parametrize("code", [401, 404, 400])
def test_another_refusal_is_a_server_or_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, code: int
) -> None:
    # Not a lost lease: the run still stops, since nobody could resume it, and the job fails naming
    # the cause, so an operator fixes the token or the server rather than chasing a reclaim.
    _answering(monkeypatch, (code, {"error": "nope"}))
    client = _Client()
    _register(client)
    project = _project(tmp_path / "project")

    job = execute_job_spec(
        _host_spec("j1", project),
        cwd=project,
        bus=InMemoryLogBus(),
        repository=None,
        one_shot=True,
        batch_checkpoint=_checkpoint("j1", "w1", None, threading.Event()),
    )

    error = str(job.view()["error"])
    assert "server or configuration error" in error and f"HTTP {code}" in error
    assert client.stopped == ["arn:run/1"]
    assert "not a lost lease" in caplog.text


def test_a_409_alone_means_a_lost_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(monkeypatch, (409, {}))
    with pytest.raises(worker_mod._CheckpointLostError):
        _checkpoint("j1", "w1", None, threading.Event()).save("arn:run/1")


def test_a_repeated_save_of_the_stored_arn_succeeds_after_a_reclaim(tmp_path: Path) -> None:
    # The first answer was lost in transit and the lease moved on; the retry must not read as a
    # lost lease, or the worker would stop a run the next attempt is about to resume.
    state, repo = _db_state(tmp_path)
    repo.enqueue_job("j1", "o", {"job_id": "j1", "batch": _batch()})
    assert repo.lease_job("w1", []) is not None
    body = {"worker_id": "w1", "job_id": "j1", "run_arn": "arn:run/1"}
    assert ops.worker_batch_run(state, body)[1] == 200
    repo.reclaim_expired_leases(timedelta(0))

    assert ops.worker_batch_run(state, body)[1] == 200
    assert ops.worker_batch_run(state, {**body, "run_arn": "arn:run/2"})[1] == 409
    assert repo.load_batch_run_arn("j1") == "arn:run/1"


def test_the_stored_application_is_the_snapshot_that_was_hashed(tmp_path: Path) -> None:
    import hashlib

    from test_devicefarm_worker import _Recording, _storage_state

    from bajutsu.serve.operations.dispatch import start_run_set

    executor = _Recording()
    state = _storage_state(tmp_path, executor)
    _payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 200
    (job,) = executor.jobs
    objects = state.object_store.objects  # type: ignore[union-attr]
    (stored,) = objects.values()
    assert hashlib.sha256(stored).hexdigest() == job.batch_app


def test_a_failed_save_stops_the_scheduled_run(tmp_path: Path) -> None:
    client = _Client()

    def save(_arn: str) -> None:
        raise RuntimeError("lease lost")

    with pytest.raises(RuntimeError, match="lease lost"):
        submit_and_collect(
            client,
            _Transfer(),
            project_arn="arn:project/1",
            device_selection={},
            app_path=_project(tmp_path / "p") / "app.apk",
            package_zip=tmp_path / "p" / "app.apk",
            spec_yaml=tmp_path / "p" / "app.apk",
            dest=tmp_path / "dest",
            sleep=lambda _s: None,
            on_scheduled=save,
        )
    assert client.stopped == ["arn:run/1"]


def test_an_interrupt_before_scheduling_schedules_nothing(tmp_path: Path) -> None:
    client = _Client()
    with pytest.raises(RunInterrupted, match="before the run was scheduled"):
        submit_and_collect(
            client,
            _Transfer(),
            project_arn="arn:project/1",
            device_selection={},
            app_path=_project(tmp_path / "p") / "app.apk",
            package_zip=tmp_path / "p" / "app.apk",
            spec_yaml=tmp_path / "p" / "app.apk",
            dest=tmp_path / "dest",
            sleep=lambda _s: None,
            interrupt=lambda: "stop",
        )
    assert client.scheduled == [] and client.stopped == []


def test_a_stop_run_refusal_is_logged_and_the_run_still_ends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Refusing(_Client):
        def stop_run(self, *, arn: str) -> dict[str, Any]:
            raise RuntimeError("throttled")

    from bajutsu.common.cloud.devicefarm import collect_run

    with pytest.raises(RunInterrupted, match="stopped run arn:run/9"):
        collect_run(
            _Refusing(),
            _Transfer(),
            run_arn="arn:run/9",
            dest=Path("unused"),
            sleep=lambda _s: None,
            interrupt=lambda: "stop",
        )
    assert "could not stop Device Farm run arn:run/9" in caplog.text


# --- a cancel stops the scheduled run --------------------------------------------------------------


def test_a_heartbeat_cancel_stops_the_run_and_posts_it_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, repo = _db_state(tmp_path)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(worker_mod, "_post_json", _route(state, calls))
    project = _project(tmp_path / "project")
    repo.enqueue_job("j1", "o", _host_spec("j1", project))
    client = _Client(on_poll=lambda _n: repo.cancel_job("j1"))
    _register(client, sleep=lambda _s: time.sleep(0.02))
    lease, _code = ops.worker_lease(state, "w1")

    result, abandoned, _work = worker_mod._run_with_heartbeat(
        lease["spec"],
        job_id="j1",
        work=tmp_path / "worker",
        bundle_urls=None,
        bus=InMemoryLogBus(),
        url="http://server",
        wid="w1",
        auth_token=None,
        heartbeat_interval=0.01,
        environment="devicefarm",
    )

    assert abandoned is False and result is not None
    assert result["cancelled"] is True
    assert client.stopped == ["arn:run/1"]
    assert repo.load_batch_run_arn("j1") == "arn:run/1"


def test_a_signal_under_worker_once_stops_the_scheduled_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path / "project")

    def interrupt_once(poll: int) -> None:
        if poll == 1:
            os.kill(os.getpid(), signal.SIGINT)

    client = _Client(on_poll=interrupt_once)

    def register(*_a: Any, **_k: Any) -> list[str]:
        _register(client, sleep=lambda _s: time.sleep(0.02))
        return ["devicefarm"]

    monkeypatch.setattr(batch_bootstrap, "register_batch_providers", register)
    worker_file = tmp_path / "w.yaml"
    worker_file.write_text("version: 1\nenvironment: devicefarm\ndrivers: [adb]\n", "utf-8")
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"batch": _batch()}), encoding="utf-8")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app, ["worker", "--worker-config", str(worker_file), "--once", str(spec_file)]
    )

    assert result.exit_code == 128 + signal.SIGINT, result.output
    assert client.stopped == ["arn:run/1"] and len(client.scheduled) == 1


def test_a_cancel_reaches_a_job_resumed_from_its_stored_arn(tmp_path: Path) -> None:
    relay = CancelRelay()
    client = _Client(on_poll=lambda _n: relay.request())
    _register(client)
    project = _project(tmp_path / "project")
    checkpoint = _checkpoint("j1", "w1", "arn:run/7", threading.Event())

    job = execute_job_spec(
        _host_spec("j1", project),
        cwd=project,
        bus=InMemoryLogBus(),
        repository=None,
        one_shot=True,
        on_job=relay.bind,
        batch_checkpoint=checkpoint,
    )

    assert job.view()["cancelled"] is True
    assert client.stopped == ["arn:run/7"] and client.scheduled == []


# --- unit 7: the worker posts the result and uploads the landed run ------------------------------


def _serve_one(state: ServeState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run `_serve_leases` until one result is posted; the keys the run's files were put under."""
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(worker_mod, "_post_json", _route(state, calls))
    put: list[str] = []
    monkeypatch.setattr(worker_mod, "_put_file", lambda url, _path, *_a, **_k: put.append(url))
    worker_mod._serve_leases(
        url="http://server",
        auth_token=None,
        wid="w1",
        caps=["environment:devicefarm"],
        work=tmp_path / "worker",
        poll_interval=0.01,
        heartbeat_interval=0.05,
        worker_file=None,
        current=worker_mod._CurrentJob(),
        stopped=lambda: any(p == "/api/worker/result" for p, _ in calls),
        environment="devicefarm",
    )
    return put


def test_the_worker_posts_the_verdict_and_uploads_the_landed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, repo = _db_state(tmp_path)
    state.object_store = FakeObjectStore()
    project = _project(tmp_path / "project")
    repo.enqueue_job("j1", "o", _host_spec("j1", project), capabilities=["environment:devicefarm"])
    client = _Client()
    client.complete.set()
    _register(client)

    put = _serve_one(state, tmp_path, monkeypatch)

    info = repo.get_job("j1")
    assert info is not None and info["status"] == "done"
    assert info["result"]["ok"] is True and info["result"]["runId"] == "20260101-1"
    assert any(url.endswith("20260101-1/manifest.json") for url in put)


def test_the_worker_posts_why_a_submission_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, repo = _db_state(tmp_path)
    project = _project(tmp_path / "project")
    (project / "app.apk").unlink()
    repo.enqueue_job("j1", "o", _host_spec("j1", project), capabilities=["environment:devicefarm"])
    _register(_Client())

    _serve_one(state, tmp_path, monkeypatch)

    info = repo.get_job("j1")
    assert info is not None and info["status"] == "failed"
    assert "not in this job's bundle" in str(info["result"].get("error"))

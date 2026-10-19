"""End-to-end faked-AWS coverage of the cloud-batch fan-out (BE-0336 Unit 6).

`test_run_set` asserts the fan-out at the dispatch layer (one `BatchRequest` per scenario) with a
recording executor, and `test_batch_provider` drives one scenario through the in-memory Device Farm
fake. Neither connects the two: nothing runs a *fan-out* through the real `DeviceFarmBatchProvider`
against the fake to a landed run and a verdict. These tests close that gap — `start_run_set` fans a
scenario set out into jobs that execute through the provider seam against the same in-memory fake the
CLI submitter uses, so the gate exercises fan-out → provider → landing → verdict without reaching
real AWS.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from _shared import queue_repository
from conftest import FakeObjectStore

from bajutsu import serve as srv
from bajutsu.serve import batch_provider as bp
from bajutsu.serve.operations.dispatch import start_run_set
from bajutsu.serve.state import Job, ServeState


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    # The provider registry is process-global; snapshot and restore it so a registration here never
    # leaks into another test (mirrors test_batch_provider's fixture).
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


class _FakeClient:
    """A minimal in-memory Device Farm client that drives the happy path and counts the schedules."""

    def __init__(self) -> None:
        self.schedule_calls = 0

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        return {"upload": {"arn": f"arn:upload/{name}", "url": f"https://s3/{name}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        return {"upload": {"arn": arn, "status": "SUCCEEDED"}}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        self.schedule_calls += 1
        return {"run": {"arn": f"arn:run/{self.schedule_calls}"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn, "status": "COMPLETED"}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {"artifacts": [{"name": "runs", "extension": "zip", "url": "https://s3/runs.zip"}]}

    def stop_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn, "status": "STOPPING"}}


class _FakeTransfer:
    """Downloads a distinct run tree per call so a fan-out's jobs each land their own run.

    A single scheduled run yields one CUSTOMER_ARTIFACT zip; across the fan-out's jobs the fake hands
    back a fresh ``runs/<id>/`` each time (an incrementing id), so serve lands one directory per
    scenario rather than colliding them on a shared id.
    """

    def __init__(self, *, manifest_ok: bool) -> None:
        self.uploaded: list[str] = []
        self._ok = manifest_ok
        self._downloads = 0

    def upload(self, url: str, path: Path) -> None:
        self.uploaded.append(url)

    def download(self, url: str) -> bytes:
        self._downloads += 1
        run_id = f"20260101-{self._downloads}"
        manifest = json.dumps({"ok": self._ok, "scenarios": [{"scenario": "a", "ok": self._ok}]})
        return _zip_bytes({f"runs/{run_id}/manifest.json": manifest})


class _RunningExecutor:
    """Runs each dispatched job inline through `run_job`, so a fan-out drives the real provider seam
    against the fake synchronously — no thread, no cloud. The finished jobs are captured so the test
    reads their verdicts directly rather than reaching into the registry's internals."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def dispatch(self, state: ServeState, job: Job) -> None:
        srv.run_job(state, job)
        self.jobs.append(job)


class _RecordingExecutor:
    """Captures dispatched jobs without running them, so they stay in
    flight (mirrors test_run_set); the test runs the captured jobs afterwards."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def dispatch(self, state: ServeState, job: Job) -> None:
        self.jobs.append(job)


def _android_batch_project(tmp_path: Path, *, scenarios: list[str]) -> tuple[Path, Path]:
    """A scenarios dir + config for an Android target, plus an APK a device cloud installs."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    body = "- name: a\n  steps:\n    - tap: { id: x }\n"
    for name in scenarios:
        (scn_dir / name).write_text(body, encoding="utf-8")
    (tmp_path / "app.apk").write_text("APK", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            "    app: {id: com.example.demo, path: app.apk}\n"
            "    paths: {scenarios: scenarios}\n"
        ),
        encoding="utf-8",
    )
    return scn_dir, cfg


def _register_devicefarm(*, manifest_ok: bool) -> tuple[_FakeClient, _FakeTransfer]:
    client = _FakeClient()
    transfer = _FakeTransfer(manifest_ok=manifest_ok)
    bp.register(
        "devicefarm",
        bp.DeviceFarmBatchProvider(
            client=client, transfer=transfer, project_arn="arn:project/1", sleep=lambda _: None
        ),
    )
    return client, transfer


def test_relative_app_path_travels_as_a_logical_reference(tmp_path: Path) -> None:
    # The request carries the target's appPath as its config writes it, not an absolute path on the
    # server's host (BE-0448): the worker resolves it against the job's bundle directory, which for a
    # config on disk is the config's own directory (BE-0242), so the upload finds the APK there.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "one.yaml").write_text(
        "- name: a\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    (tmp_path / "app.apk").write_text("APK", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            "    app: {id: com.example.demo, path: app.apk}\n"
            "    paths: {scenarios: scenarios}\n"
        ),  # relative to the config's own directory (tmp_path)
        encoding="utf-8",
    )
    client, _ = _register_devicefarm(manifest_ok=True)
    executor = _RunningExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
        repository=queue_repository(),
        object_store=FakeObjectStore(),
    )

    _payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 200
    (job,) = executor.jobs
    assert job.batch is not None
    assert job.batch.app_path == "app.apk"
    assert client.schedule_calls == 1 and job.view()["ok"] is True


def test_request_paths_are_relative_to_the_config_directory(tmp_path: Path) -> None:
    # The config's directory is the bundle the worker packages (BE-0448), so the config and the
    # scenarios travel as paths relative to it, whatever directory the server itself runs from.
    proj = tmp_path / "proj"
    scn_dir = proj / "scenarios"
    scn_dir.mkdir(parents=True)
    (scn_dir / "one.yaml").write_text(
        "- name: a\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    cfg = proj / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            "    app: {id: com.example.demo, path: app.apk}\n"
            "    paths: {scenarios: scenarios}\n"
        ),
        encoding="utf-8",
    )
    (proj / "app.apk").write_text("APK", encoding="utf-8")
    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=proj,
        executor=executor,
        repository=queue_repository(),
        object_store=FakeObjectStore(),
    )

    _payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 200
    (job,) = executor.jobs
    assert job.batch is not None
    assert job.batch.config == "bajutsu.config.yaml"
    assert job.batch.scenario == "scenarios/one.yaml"
    # The config's directory travels as the job's bundle, which is what a worker packages.
    assert job.bundle is not None
    assert job.capabilities == ["environment:devicefarm"]


def test_a_scenario_outside_the_config_directory_is_refused(tmp_path: Path) -> None:
    # A scenario the bundle does not hold would travel as `../…` and be missing on the cloud host.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "one.yaml").write_text("- name: a\n  steps: []\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = proj / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            f"    app: {{id: com.example.demo, path: app.apk}}\n"
            f"    paths: {{scenarios: {scn_dir}}}\n"
        ),
        encoding="utf-8",
    )
    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=proj,
        executor=executor,
        repository=queue_repository(),
        object_store=FakeObjectStore(),
    )

    payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 400
    assert "not under the config's directory" in payload["error"]
    assert executor.jobs == []


def test_fan_out_runs_every_scenario_through_the_device_farm_fake(tmp_path: Path) -> None:
    # The whole path, faked end to end: start_run_set fans three scenarios out into three cloud-batch
    # jobs, each runs through the real DeviceFarmBatchProvider against the in-memory Device Farm fake,
    # and each lands its own run under runs_dir with a PASS verdict — one schedule per scenario, no
    # collision, off the `run`/CI verdict path (BE-0336).
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    client, _ = _register_devicefarm(manifest_ok=True)
    executor = _RunningExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
        repository=queue_repository(),
        object_store=FakeObjectStore(),
    )

    payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 200
    assert len(payload["jobIds"]) == 3
    assert client.schedule_calls == 3  # one Device Farm run scheduled per scenario
    # Each job ran through the fake to a PASS verdict and landed a distinct run under runs_dir.
    landed = sorted(p.parent.name for p in (tmp_path / "runs").glob("*/manifest.json"))
    assert landed == ["20260101-1", "20260101-2", "20260101-3"]
    assert len(executor.jobs) == 3
    for job in executor.jobs:
        view = job.view()
        assert view["status"] == "done" and view["exitCode"] == 0 and view["ok"] is True


def test_fan_out_surfaces_a_failing_manifest_from_the_fake(tmp_path: Path) -> None:
    # The verdict travels the whole faked path: a Device Farm run whose manifest reports a failure
    # surfaces as a failed job (exitCode 1, ok False) — serve reads pass/fail from Bajutsu's own
    # manifest.json, never from Device Farm's own PASSED/FAILED classification (prime directive 1).
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    _register_devicefarm(manifest_ok=False)
    executor = _RunningExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
        repository=queue_repository(),
        object_store=FakeObjectStore(),
    )

    payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 200
    assert len(payload["jobIds"]) == 1
    (job,) = executor.jobs
    view = job.view()
    assert view["status"] == "done" and view["exitCode"] == 1 and view["ok"] is False


def test_every_scenario_of_a_faked_fan_out_lands(tmp_path: Path) -> None:
    # The fan-out and real faked runs together: no server-side budget defers a scenario (BE-0452;
    # the worker's `maxJobConcurrency` bounds its jobs), so three scenarios dispatch three jobs, and
    # running them through the fake lands three runs.
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    client, _ = _register_devicefarm(manifest_ok=True)
    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
        repository=queue_repository(),
        object_store=FakeObjectStore(),
    )

    payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})

    assert status == 200
    assert len(payload["jobIds"]) == 3
    assert len(executor.jobs) == 3

    # Now run the dispatched jobs through the fake: each schedules its own run and lands a verdict.
    for job in executor.jobs:
        srv.run_job(state, job)
    assert client.schedule_calls == 3
    landed = sorted(p.parent.name for p in (tmp_path / "runs").glob("*/manifest.json"))
    assert landed == ["20260101-1", "20260101-2", "20260101-3"]
    for job in executor.jobs:
        view = job.view()
        assert view["status"] == "done" and view["exitCode"] == 0 and view["ok"] is True

"""Tests for the per-scenario cloud-batch fan-out dispatch (BE-0336 Unit 3).

`start_run_set` expands one scenario-set request into one cloud-batch job per scenario, each carrying
its own `BatchRequest`, registered through the same concurrency-capped tail as every other run. A
recording executor captures the dispatched jobs so the fan-out is asserted without actually running
them (no thread, no cloud).
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from _shared import _post, _serve, queue_repository
from conftest import FakeObjectStore

from bajutsu import serve as srv
from bajutsu.serve.operations.dispatch import start_run_set
from bajutsu.serve.state import Job, ServeState


class _RecordingExecutor:
    """Captures dispatched jobs instead of running them, so a fan-out test stays synchronous."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def dispatch(self, state: ServeState, job: Job) -> None:
        self.jobs.append(job)


def _android_batch_project(tmp_path: Path, *, scenarios: list[str]) -> tuple[Path, Path]:
    """A scenarios dir + config for an Android target with an app a device cloud can install."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    body = "- name: a\n  steps:\n    - tap: { id: x }\n"
    for name in scenarios:
        (scn_dir / name).write_text(body, encoding="utf-8")
    (tmp_path / "app.apk").write_text("APK", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    # Relative paths, as the config's directory travels to the worker as a bundle (BE-0465).
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


# Where the runs go is the request's choice (BE-0452), named as a batch-provider environment.
_DF = {"target": "demo", "environment": "devicefarm"}


def _state(tmp_path: Path, cfg: Path, scn_dir: Path) -> tuple[ServeState, _RecordingExecutor]:
    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
        repository=queue_repository(),
    )
    state.object_store = FakeObjectStore()  # the bundle of the config's directory lands here
    return state, executor


def test_fan_out_registers_one_batch_job_per_scenario(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, _DF)

    assert status == 200
    assert len(payload["jobIds"]) == 3
    # Every dispatched job carries a per-scenario BatchRequest — one scenario each, not the whole set.
    dispatched: list[str] = []
    for job in executor.jobs:
        assert job.batch is not None
        dispatched.append(job.batch.scenario)
    scenarios = sorted(dispatched)
    assert scenarios == [
        "scenarios/one.yaml",
        "scenarios/three.yaml",
        "scenarios/two.yaml",
    ]
    for job in executor.jobs:
        assert job.batch is not None
        assert job.batch.provider == "devicefarm"
        assert job.batch.target == "demo"
        assert job.batch.platform == "android"
        assert job.batch.config == "bajutsu.config.yaml"
        assert job.batch.app_path == "app.apk"


def test_fan_out_honours_an_explicit_scenario_subset(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {**_DF, "scenarios": ["two.yaml"]})

    assert status == 200
    assert len(payload["jobIds"]) == 1
    batch = executor.jobs[0].batch
    assert batch is not None and batch.scenario == "scenarios/two.yaml"


@pytest.mark.parametrize(
    ("environment", "why"),
    [
        (None, "missing"),
        ("appium", "an appium grid is reached through the plain run request"),
        ("local", "not a batch provider"),
        ("firebase", "unknown"),
    ],
)
def test_fan_out_needs_a_batch_provider_environment(
    tmp_path: Path, environment: str | None, why: str
) -> None:
    # The fan-out packages the app for a device cloud, so it takes a batch-provider kind alone; the
    # target no longer names one (BE-0452).
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)
    body: dict[str, object] = {"target": "demo"}
    if environment is not None:
        body["environment"] = environment

    payload, status = start_run_set(state, body)

    assert status == 400, why
    assert "environment naming a device-cloud batch provider" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_the_removed_device_budget(tmp_path: Path) -> None:
    # The worker's `maxJobConcurrency` bounds a device cloud's jobs (BE-0448); a request still
    # naming `deviceBudget` fails loudly rather than having its cap silently ignored (BE-0452).
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {**_DF, "deviceBudget": 1})

    assert status == 400
    assert "maxJobConcurrency" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_a_web_target(tmp_path: Path) -> None:
    # Device-cloud batch runs on a physical android/ios device; a web target has no app to install.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "one.yaml").write_text(
        "- name: a\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: web\n"
            f"    app: {{url: 'http://localhost:8080'}}\n"
            f"    paths: {{scenarios: {scn_dir}}}\n"
        ),
        encoding="utf-8",
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, _DF)

    assert status == 400
    assert "android or ios" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_an_unknown_scenario_before_dispatching_any(tmp_path: Path) -> None:
    # A requested scenario that isn't in the target's dir fails the whole request closed — no partial
    # fan-out that dispatched the good ones and errored on the bad one.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {**_DF, "scenarios": ["one.yaml", "ghost.yaml"]})

    assert status == 400
    assert "ghost.yaml" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_a_target_with_no_app_to_install(tmp_path: Path) -> None:
    # A cloud-batch run installs an app on the reserved device; a target with no app.path is refused
    # loudly rather than submitting a run that can't install anything.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "one.yaml").write_text(
        "- name: a\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            f"    app: {{id: com.example.demo}}\n"
            f"    paths: {{scenarios: {scn_dir}}}\n"
        ),
        encoding="utf-8",
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, _DF)

    assert status == 400
    assert "app.path" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_an_empty_scenario_subset(tmp_path: Path) -> None:
    # An explicit empty list has nothing to run; a spec that runs nothing would silently "pass", so
    # the request fails closed instead.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {**_DF, "scenarios": []})

    assert status == 400
    assert "no scenarios" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_a_config_outside_the_run_directory(tmp_path: Path) -> None:
    # The worker packages the job's bundle directory (state.binding.cwd), so a config living outside
    # it would travel as a `../…` path the cloud host can't find. Fail loud at the endpoint.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    run_dir = tmp_path / "elsewhere"
    run_dir.mkdir()
    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=run_dir,  # the config (under tmp_path) is not under this run dir
        executor=executor,
        repository=queue_repository(),
    )

    payload, status = start_run_set(state, _DF)

    assert status == 400
    assert "not under its bundle directory" in payload["error"]
    assert executor.jobs == []


def test_fan_out_requires_an_open_config(tmp_path: Path) -> None:
    state = ServeState(
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=_RecordingExecutor(),
        repository=queue_repository(),
    )
    payload, status = start_run_set(state, _DF)
    assert status == 400
    assert "config" in payload["error"]


def test_fan_out_returns_429_when_cap_rejects_the_first_job(tmp_path: Path) -> None:
    # When the concurrency cap rejects the very first job (nothing dispatched yet), the endpoint
    # returns the 429 payload rather than 200 with an empty list — a silent failure that looks like
    # success. Partial dispatch (some dispatched, then capped) is routine (Unit 4 governs this), but
    # zero dispatched + cap = loud error, consistent with start_run / start_record / start_crawl.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])

    # Make try_register always return None (cap hit) by wrapping ServeState.
    import unittest.mock

    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=_RecordingExecutor(),
        repository=queue_repository(),
    )
    state.object_store = FakeObjectStore()
    with unittest.mock.patch.object(state, "try_register", return_value=None):
        payload, status = start_run_set(state, _DF)

    assert status == 429
    assert "too many concurrent" in payload["error"]


def test_fan_out_dispatches_every_scenario(tmp_path: Path) -> None:
    # No server-side budget remains (BE-0452): the worker's `maxJobConcurrency` bounds its jobs.
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, _ = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, _DF)

    assert status == 200
    assert len(payload["jobIds"]) == 3


def test_fan_out_ships_scenarios_that_travel_as_materials(tmp_path: Path) -> None:
    # On the server-backed scenario store, runnable.arg is already workspace-relative and the
    # scenario text travels as materials (BE-0448). The job ships it with the config, and the
    # request names both by their place in the worker's workspace, which is the bundle it packages.
    from bajutsu.serve.scenarios import Runnable

    # A worker without the project receives the binary by signed URL, so the app.path names a file
    # inside the config's directory and the server has an object store to deliver it through.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])

    class _MaterialsScope:
        """A fake scope whose runnable carries materials (server-backed scenario store)."""

        def list(self):
            return [{"file": "one.yaml"}]

        def runnable(self, name: str) -> Runnable:
            return Runnable(
                arg="one.yaml",
                materials={"one.yaml": "- name: a\n  steps:\n    - tap: { id: x }\n"},
            )

    class _MaterialsStore:
        def scope(self, app, *, session=None, org=""):
            return _MaterialsScope()

    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
        repository=queue_repository(),
    )
    state.object_store = FakeObjectStore()
    # Inject the materials-returning scope via for_org().scenarios
    import unittest.mock

    mock_bundle = unittest.mock.MagicMock()
    mock_bundle.scenarios = _MaterialsStore()
    with unittest.mock.patch.object(state, "for_org", return_value=mock_bundle):
        payload, status = start_run_set(state, _DF)

    assert status == 200, payload
    (job,) = executor.jobs
    assert job.batch is not None
    assert (job.batch.scenario, job.batch.config) == ("one.yaml", "bajutsu.config.yaml")
    assert job.materials["one.yaml"].startswith("- name: a")
    assert job.materials["bajutsu.config.yaml"] == cfg.read_text(encoding="utf-8")
    assert job.batch_app is not None  # the stored binary the lease signs for the worker


def test_run_set_endpoint_is_wired(tmp_path: Path) -> None:
    # The POST /api/run-set route reaches start_run_set end to end through the real server: a
    # scenario-set request returns one job id per scenario, queued for a worker to lease.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=_RecordingExecutor(),
        repository=queue_repository(),
    )
    state.object_store = FakeObjectStore()
    server, port = _serve(state)
    try:
        status, payload = _post(port, "/api/run-set", _DF)
        assert status == 200
        assert len(payload["jobIds"]) == 2
    finally:
        server.shutdown()
        server.server_close()


def _local_bundle_project(tmp_path: Path, *, config_name: str = "bajutsu.config.yaml") -> Path:
    """A config directory as a developer keeps it: relative paths, a fixture beside the scenarios,
    and the scratch output (`runs/`, `tmp/`) a Device Farm package leaves out."""
    (tmp_path / "scenarios" / "fixtures").mkdir(parents=True)
    for name in ("one.yaml", "two.yaml"):
        (tmp_path / "scenarios" / name).write_text(
            "- name: a\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
        )
    (tmp_path / "scenarios" / "fixtures" / "photo.png").write_bytes(b"PNG")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "app.apk").write_bytes(b"APK")
    (tmp_path / "runs" / "old").mkdir(parents=True)
    (tmp_path / "runs" / "old" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "app.apk").write_bytes(b"APK")
    cfg = tmp_path / config_name
    cfg.write_text(
        "targets:\n"
        "  demo:\n"
        "    platform: android\n"
        "    app: {id: com.example.demo, path: build/app.apk}\n"
        "    paths: {scenarios: scenarios}\n",
        encoding="utf-8",
    )
    return cfg


def _queued_state(tmp_path: Path, cfg: Path) -> ServeState:
    """A state whose jobs go through the real queue executor of a server on machine ``m1``."""
    from bajutsu.serve.server.db_executor import DbQueueExecutor

    repository = queue_repository()
    state = ServeState(
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=DbQueueExecutor(repository, machine_id="m1"),
        repository=repository,
    )
    state.object_store = FakeObjectStore()
    return state


def _stored_zip(state: ServeState, job: Job) -> zipfile.ZipFile:
    from bajutsu.serve.server.object_store import upload_store_key

    assert job.bundle is not None and state.object_store is not None
    data = state.object_store.get_bytes(upload_store_key("", job.org or "", job.bundle["id"]))
    assert data is not None
    assert hashlib.sha256(data).hexdigest() == job.bundle["id"]
    return zipfile.ZipFile(io.BytesIO(data))


def test_fan_out_ships_a_local_config_directory_as_one_bundle(tmp_path: Path) -> None:
    # A config on the server's disk travels as a bundle of its whole directory, so a Device Farm
    # worker on any machine can lease the job (no `machine:` pin) and the scenario still finds the
    # files it references (BE-0465).
    from bajutsu.serve.server.worker_job import job_spec

    state = _queued_state(tmp_path, _local_bundle_project(tmp_path))

    payload, status = start_run_set(state, _DF)

    assert status == 200, payload
    jobs = [state.jobs[i] for i in payload["jobIds"]]
    assert len(jobs) == 2
    bundle = jobs[0].bundle
    assert bundle is not None
    assert all(job.bundle == bundle for job in jobs)  # one zip per fan-out, shared by its jobs
    assert bundle["artifacts"] is None and bundle["scenarios_filename"] is None
    names = set(_stored_zip(state, jobs[0]).namelist())
    assert {
        "bajutsu.config.yaml",
        "scenarios/one.yaml",
        "scenarios/fixtures/photo.png",
        "build/app.apk",
    } <= names
    assert not any(n.startswith(("runs/", "tmp/")) for n in names)
    for job in jobs:
        assert job.materials == {}
        assert job.batch_app is None  # the bundle carries the binary
        assert job_spec(job)["cwd"] is None
        assert job.capabilities == ["environment:devicefarm"]


def test_fan_out_names_a_bundled_config_by_the_name_a_worker_finds(tmp_path: Path) -> None:
    # A worker finds a bundle's config by its conventional name, so a config saved under another
    # name travels as bajutsu.config.yaml and the request points there.
    cfg = _local_bundle_project(tmp_path, config_name="df.config.yaml")
    state = _queued_state(tmp_path, cfg)

    payload, status = start_run_set(state, _DF)

    assert status == 200, payload
    job = state.jobs[payload["jobIds"][0]]
    assert job.batch is not None and job.batch.config == "bajutsu.config.yaml"
    archive = _stored_zip(state, job)
    assert archive.read("bajutsu.config.yaml").decode("utf-8") == cfg.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("build/app.apk", "tmp/app.apk", "tmp/app.apk"),
        ("build/app.apk", "build/missing.apk", "build/missing.apk"),
        # An absolute path works on this machine and nowhere the bundle lands.
        ("{scenarios: scenarios}", "{scenarios: ABS/scenarios}", "demo"),
    ],
)
def test_fan_out_refuses_a_local_config_its_bundle_cannot_carry(
    tmp_path: Path, old: str, new: str, expected: str
) -> None:
    # The worker would only find out on the device cloud, after the upload; refuse at the request.
    cfg = _local_bundle_project(tmp_path)
    edited = cfg.read_text(encoding="utf-8").replace(old, new.replace("ABS", str(tmp_path)))
    cfg.write_text(edited, encoding="utf-8")
    state = _queued_state(tmp_path, cfg)

    payload, status = start_run_set(state, {**_DF, "scenarios": ["one.yaml"]})

    assert status == 400, payload
    assert expected in payload["error"]
    assert state.jobs == {}


def test_fan_out_of_a_local_config_needs_an_object_store(tmp_path: Path) -> None:
    state = _queued_state(tmp_path, _local_bundle_project(tmp_path))
    state.object_store = None

    payload, status = start_run_set(state, _DF)

    assert status == 400
    assert "object store" in payload["error"]
    assert state.jobs == {}


def test_fan_out_of_a_local_config_reports_a_store_that_cannot_take_the_bundle(
    tmp_path: Path,
) -> None:
    state = _queued_state(tmp_path, _local_bundle_project(tmp_path))
    assert isinstance(state.object_store, FakeObjectStore)
    state.object_store.fail_with = OSError("bucket unreachable")

    payload, status = start_run_set(state, _DF)

    assert status == 502
    assert "bucket unreachable" in payload["error"]
    assert state.jobs == {}


def test_fan_out_needs_the_config_at_the_root_of_its_bundle(tmp_path: Path) -> None:
    # The archive's root is the server's directory; a config below it would travel where a worker
    # never looks for one.
    cfg = _local_bundle_project(tmp_path)
    (tmp_path / "conf").mkdir()
    state = _queued_state(tmp_path, cfg.rename(tmp_path / "conf" / cfg.name))

    payload, status = start_run_set(state, _DF)

    assert status == 400, payload
    assert "not at the root of its directory" in payload["error"]
    assert state.jobs == {}

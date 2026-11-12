"""Tests for the per-scenario fan-out dispatch (BE-0336 Unit 3, BE-0484).

`start_run_set` expands one scenario-set request into one job per scenario, each carrying its own run
intent, and writes them as one group straight to the queue: no executor runs them, and the server's
concurrency caps apply when a worker leases them (BE-0484 unit 3).
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from _shared import _get_json, _post, _serve, queue_repository
from conftest import FakeObjectStore

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.capabilities import RUN_INTENT_CAPABILITY
from bajutsu.serve.operations.dispatch import start_run_set
from bajutsu.serve.server.db import QueuedJob
from bajutsu.serve.state import Job, ServeState


class _RecordingExecutor:
    """Captures dispatched jobs instead of running them; a fan-out must hand it none."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def dispatch(self, state: ServeState, job: Job) -> None:
        self.jobs.append(job)


class _Registered:
    """The jobs a fan-out registered, in id order, read off the state's registry."""

    def __init__(self, state: ServeState) -> None:
        self._state = state

    @property
    def jobs(self) -> list[Job]:
        return sorted(self._state.jobs.values(), key=lambda j: int(j.id))


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
            "    subject: {id: com.example.demo, path: app.apk}\n"
            "    paths: {scenarios: scenarios}\n"
        ),
        encoding="utf-8",
    )
    return scn_dir, cfg


# Where the runs go is the request's choice (BE-0452), named as a batch-provider environment.
_DF = {"target": "demo", "environment": "devicefarm"}


def _state(tmp_path: Path, cfg: Path, scn_dir: Path) -> tuple[ServeState, _Registered]:
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
    return state, _Registered(state)


def test_fan_out_registers_one_batch_job_per_scenario(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, _DF)

    assert status == 200
    assert len(payload["jobIds"]) == 3
    # Every dispatched job carries a per-scenario run intent — one scenario each, not the whole set.
    dispatched: list[str] = []
    for job in executor.jobs:
        assert job.intent is not None
        dispatched.append(job.intent.scenario)
    scenarios = sorted(dispatched)
    assert scenarios == [
        "scenarios/one.yaml",
        "scenarios/three.yaml",
        "scenarios/two.yaml",
    ]
    for job in executor.jobs:
        # The intent names no provider; the environment token still routes it until BE-0484 unit 5.
        assert job.batch is None and job.intent is not None
        assert job.capabilities == ["environment:devicefarm", RUN_INTENT_CAPABILITY]
        assert job.intent.target == "demo"
        assert job.intent.platform == "android"
        assert job.intent.config == "bajutsu.config.yaml"
        assert job.intent.app_path == "app.apk"


def test_a_local_binding_copies_the_stored_baselines_and_names_no_upload_policy(
    tmp_path: Path,
) -> None:
    # The values the server alone decides travel with the intent, as for a plain run (BE-0484).
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    _payload, status = start_run_set(state, _DF)

    assert status == 200
    (job,) = executor.jobs
    assert job.intent is not None and job.intent.upload_exec == ""
    assert job.materialize_baselines is True
    assert job.provenance is None


def test_an_uploaded_binding_carries_its_launch_policy_and_provenance_but_no_baselines(
    tmp_path: Path,
) -> None:
    upload = SimpleNamespace(
        worker_ref={"id": "a" * 64}, sha256="a" * 64, provenance={"bundle": "a" * 64}
    )
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)
    state.binding = replace(state.binding, upload=upload)  # type: ignore[arg-type]
    state.upload_exec = "deny"

    _payload, status = start_run_set(state, _DF)

    assert status == 200
    (job,) = executor.jobs
    assert job.intent is not None and job.intent.upload_exec == "deny"
    assert job.materialize_baselines is False
    assert job.provenance == {"bundle": "a" * 64}


def test_fan_out_honours_an_explicit_scenario_subset(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {**_DF, "scenarios": ["two.yaml"]})

    assert status == 200
    assert len(payload["jobIds"]) == 1
    intent = executor.jobs[0].intent
    assert intent is not None and intent.scenario == "scenarios/two.yaml"


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
            f"    subject: {{url: 'http://localhost:8080'}}\n"
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
    # A cloud-batch run installs an app on the reserved device; a target with no subject.path is refused
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
            f"    subject: {{id: com.example.demo}}\n"
            f"    paths: {{scenarios: {scn_dir}}}\n"
        ),
        encoding="utf-8",
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, _DF)

    assert status == 400
    assert "subject.path" in payload["error"]
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
    assert state.jobs == {}


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


def test_fan_out_registers_every_scenario_past_the_caps(tmp_path: Path) -> None:
    """The caps apply when a worker leases a group's jobs, so none cuts the group short."""
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, registered = _state(tmp_path, cfg, scn_dir)
    state.max_concurrent = state.max_concurrent_per_org = 1
    state.register(Job(cmd=[]))  # a plain run already fills the caps

    payload, status = start_run_set(state, _DF)

    assert status == 200
    assert len(payload["jobIds"]) == 3
    assert [j.id for j in registered.jobs] == ["1", *payload["jobIds"]]


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

    # A worker without the project receives the binary by signed URL, so the subject.path names a file
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
    (job,) = _Registered(state).jobs
    assert executor.jobs == []
    assert job.intent is not None
    assert (job.intent.scenario, job.intent.config) == ("one.yaml", "bajutsu.config.yaml")
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
        "    subject: {id: com.example.demo, path: build/app.apk}\n"
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
        assert job.capabilities == ["environment:devicefarm", RUN_INTENT_CAPABILITY]


def test_fan_out_names_a_bundled_config_by_the_name_a_worker_finds(tmp_path: Path) -> None:
    # A worker finds a bundle's config by its conventional name, so a config saved under another
    # name travels as bajutsu.config.yaml and the request points there.
    cfg = _local_bundle_project(tmp_path, config_name="df.config.yaml")
    state = _queued_state(tmp_path, cfg)

    payload, status = start_run_set(state, _DF)

    assert status == 200, payload
    job = state.jobs[payload["jobIds"][0]]
    assert job.intent is not None and job.intent.config == "bajutsu.config.yaml"
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


def test_fan_out_registers_one_group_its_jobs_share(tmp_path: Path) -> None:
    """BE-0484: the response names the group, whose jobs are the ones the request queued."""
    state = _queued_state(tmp_path, _local_bundle_project(tmp_path))
    assert state.repository is not None

    payload, status = start_run_set(state, {**_DF, "scenarios": ["one.yaml", "two.yaml"]})

    assert status == 200
    group = state.repository.get_job_group(payload["groupId"])
    assert group is not None and group["org_id"] == "default" and group["key"]
    assert [j["id"] for j in state.repository.group_jobs(payload["groupId"])] == payload["jobIds"]
    jobs = [state.jobs[i] for i in payload["jobIds"]]
    assert {j.group_id for j in jobs} == {payload["groupId"]}
    assert len({j.compat_key for j in jobs}) == 1 and jobs[0].compat_key


def test_a_new_request_is_a_new_group_and_shares_a_compat_key_until_the_config_changes(
    tmp_path: Path,
) -> None:
    cfg = _local_bundle_project(tmp_path)
    state = _queued_state(tmp_path, cfg)

    first, _ = start_run_set(state, {**_DF, "scenarios": ["one.yaml"]})
    second, _ = start_run_set(state, {**_DF, "scenarios": ["two.yaml"]})
    cfg.write_text(cfg.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    third, _ = start_run_set(state, {**_DF, "scenarios": ["one.yaml"]})

    assert first["groupId"] != second["groupId"]
    keys = [state.jobs[p["jobIds"][0]].compat_key for p in (first, second, third)]
    assert keys[0] == keys[1] != keys[2]


def test_fan_out_refuses_a_scenario_named_twice(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {**_DF, "scenarios": ["one.yaml", "one.yaml"]})

    assert status == 400
    assert "named more than once" in payload["error"]
    assert executor.jobs == []


def test_a_store_error_registers_no_job(tmp_path: Path) -> None:
    import unittest.mock

    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state, _ = _state(tmp_path, cfg, scn_dir)
    failing = unittest.mock.patch.object(
        state.repository, "register_job_group", side_effect=RuntimeError("database is down")
    )

    with failing, pytest.raises(RuntimeError, match="database is down"):
        start_run_set(state, _DF)

    assert state.jobs == {}


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, 7])
def test_fan_out_refuses_a_malformed_group_key(tmp_path: Path, key: object) -> None:
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, _ = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {**_DF, "groupKey": key})

    assert status == 400
    assert "groupKey" in payload["error"]


def test_a_repeated_group_key_returns_its_group_and_queues_nothing_new(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state, registered = _state(tmp_path, cfg, scn_dir)
    request = {**_DF, "groupKey": "gha/123/1:test-0"}

    first, _ = start_run_set(state, request)
    again, status = start_run_set(state, {**request, "scenarios": ["two.yaml", "one.yaml"]})

    assert status == 200
    assert again == first
    assert len(registered.jobs) == 2


def test_a_group_key_reused_for_a_different_request_is_a_conflict(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state, registered = _state(tmp_path, cfg, scn_dir)
    request = {**_DF, "groupKey": "ci-1"}
    start_run_set(state, request)

    payload, status = start_run_set(state, {**request, "scenarios": ["one.yaml"]})

    assert status == 409
    assert "ci-1" in payload["error"]
    assert len(registered.jobs) == 2


def test_a_machine_registers_its_group_under_its_own_org(tmp_path: Path) -> None:
    cfg = _local_bundle_project(tmp_path)
    with cfg.open("a", encoding="utf-8") as f:
        f.write("orgs:\n  acme:\n    targets: [demo]\n")
    state = _queued_state(tmp_path, cfg)
    assert state.repository is not None

    payload, status = start_run_set(
        state, {**_DF, "scenarios": ["one.yaml"]}, machine_org="acme", ci_job={"run": "1"}
    )

    assert status == 200
    group = state.repository.get_job_group(payload["groupId"])
    assert group is not None and group["org_id"] == "acme"
    assert state.jobs[payload["jobIds"][0]].org == "acme"


def _group_of_two(tmp_path: Path) -> ServeState:
    """A queued group ``g1`` of jobs ``1`` and ``2`` in the default org (BE-0484)."""
    state = ServeState(
        runs_dir=tmp_path / "runs", executor=_RecordingExecutor(), repository=queue_repository()
    )
    assert state.repository is not None
    jobs = [QueuedJob(id=i, spec={}) for i in ("1", "2")]
    state.repository.register_job_group(
        "g1", org_id="default", key="k1", digest="d", compat_key=None, jobs=jobs
    )
    return state


def _finish(state: ServeState, *exit_codes: int | str | None) -> None:
    """Lease and finish jobs ``1`` then ``2``: an exit code completes one, None fails it, and
    ``"cancel"`` reports it cancelled the way a worker does, with a non-zero exit code."""
    repo = state.repository
    assert repo is not None and len(exit_codes) <= 2
    for job_id, code in zip(("1", "2"), exit_codes, strict=False):
        leased = repo.lease_job("w1")
        assert leased is not None and leased.id == job_id  # lease_job ties on created_at
        if code is None:
            repo.fail_job(leased.id, "worker lost the device")
        elif code == "cancel":
            repo.complete_job(leased.id, {"exitCode": -15, "cancelled": True})
        else:
            repo.complete_job(leased.id, {"exitCode": code, "runId": f"run-{leased.id}"})


def test_a_queued_group_reads_as_pending_with_each_jobs_status(tmp_path: Path) -> None:
    payload, status = ops.job_group_view(_group_of_two(tmp_path), "g1")

    assert status == 200
    assert payload == {
        "groupId": "g1",
        "groupKey": "k1",
        "conclusion": "pending",
        "jobs": [
            {"id": "1", "status": "queued", "exitCode": None, "runId": None},
            {"id": "2", "status": "queued", "exitCode": None, "runId": None},
        ],
    }


@pytest.mark.parametrize(
    ("exit_codes", "conclusion"),
    [
        ((0,), "pending"),  # one job still queued
        ((0, 0), "success"),
        ((0, 1), "failure"),  # a job whose run finished with failing assertions
        ((0, None), "failure"),  # a job that never produced a verdict
        ((1, "cancel"), "failure"),  # a failure outranks a cancel
        ((0, "cancel"), "cancelled"),
    ],
)
def test_a_groups_conclusion_comes_from_its_jobs_exit_codes(
    tmp_path: Path, exit_codes: tuple[int | str | None, ...], conclusion: str
) -> None:
    state = _group_of_two(tmp_path)
    _finish(state, *exit_codes)

    payload, _ = ops.job_group_view(state, "g1")

    assert payload["conclusion"] == conclusion
    assert payload["jobs"][0]["runId"] == "run-1"


def test_another_orgs_group_and_an_unknown_one_both_read_as_not_found(tmp_path: Path) -> None:
    state = _group_of_two(tmp_path)

    assert ops.job_group_view(state, "g1", machine_org="acme")[1] == 404
    assert ops.job_group_view(state, "nope")[1] == 404
    assert ops.cancel_job_group(state, "g1", machine_org="acme")[1] == 404
    assert ops.cancel_job_group(state, "nope")[1] == 404


def test_cancelling_a_group_cancels_its_unfinished_jobs_only(tmp_path: Path) -> None:
    state = _group_of_two(tmp_path)
    _finish(state, 0)

    payload, status = ops.cancel_job_group(state, "g1")

    assert (payload, status) == ({"groupId": "g1", "cancelled": ["2"]}, 200)
    view, _ = ops.job_group_view(state, "g1")
    assert [j["status"] for j in view["jobs"]] == ["done", "cancelled"]
    assert view["conclusion"] == "cancelled"
    assert ops.cancel_job_group(state, "g1") == ({"groupId": "g1", "cancelled": []}, 200)


def test_the_group_routes_are_wired(tmp_path: Path) -> None:
    state = _group_of_two(tmp_path)
    server, port = _serve(state)
    try:
        assert _get_json(port, "/api/run-set/g1")["conclusion"] == "pending"
        status, payload = _post(port, "/api/run-set/g1/cancel", {})
        assert (status, payload["cancelled"]) == (200, ["1", "2"])
    finally:
        server.shutdown()
        server.server_close()


def test_a_person_reaches_a_group_through_membership_not_the_active_org(tmp_path: Path) -> None:
    """As for a single job: switching orgs never hides a group, and only an editor cancels it."""
    state = _group_of_two(tmp_path)
    repo = state.repository
    assert repo is not None
    for org, member in (("default", "alice"), ("globex", "bob")):
        repo.ensure_org(org, slug=org, name=org)
        repo.upsert_user(member, org_id=org, github_login=member, email=f"{member}@x")
        repo.set_user_orgs(member, {org: "editor"})

    assert ops.job_group_view(state, "g1", actor="bob")[1] == 404
    assert ops.cancel_job_group(state, "g1", actor="bob")[1] == 404
    repo.set_user_orgs("bob", {"globex": "editor", "default": "viewer"})
    assert ops.job_group_view(state, "g1", actor="bob")[1] == 200
    assert ops.cancel_job_group(state, "g1", actor="bob")[1] == 404
    repo.set_user_orgs("alice", {"default": "editor", "globex": "editor"})
    repo.upsert_user("alice", org_id="globex", github_login="alice", email="alice@x")
    assert ops.job_group_view(state, "g1", actor="alice")[1] == 200
    assert ops.cancel_job_group(state, "g1", actor="alice")[0]["cancelled"] == ["1", "2"]


def test_a_cancelled_leased_job_keeps_the_group_pending_until_its_worker_stops(
    tmp_path: Path,
) -> None:
    state = _group_of_two(tmp_path)
    assert state.repository is not None
    assert state.repository.lease_job("w1") is not None

    assert ops.cancel_job_group(state, "g1")[0]["cancelled"] == ["1", "2"]

    view, _ = ops.job_group_view(state, "g1")
    assert [j["status"] for j in view["jobs"]] == ["leased", "cancelled"]
    assert view["conclusion"] == "pending"

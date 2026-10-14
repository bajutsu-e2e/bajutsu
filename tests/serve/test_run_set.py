"""Tests for the per-scenario cloud-batch fan-out dispatch (BE-0336 Unit 3).

`start_run_set` expands one scenario-set request into one cloud-batch job per scenario, each carrying
its own `BatchRequest`, registered through the same concurrency-capped tail as every other run. A
recording executor captures the dispatched jobs so the fan-out is asserted without actually running
them (no thread, no cloud).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shared import _post, _serve, queue_repository

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
    apk = tmp_path / "app.apk"
    apk.write_text("APK", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            f"    app: {{id: com.example.demo, path: {apk}}}\n"
            f"    paths: {{scenarios: {scn_dir}}}\n"
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
        assert job.batch.app_path == str(tmp_path / "app.apk")


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
    from conftest import FakeObjectStore

    from bajutsu.serve.scenarios import Runnable

    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    # A worker without the project receives the binary by signed URL, so the app.path names a file
    # inside the config's directory and the server has an object store to deliver it through.
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(str(tmp_path / "app.apk"), "app.apk"),
        encoding="utf-8",
    )

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
    server, port = _serve(state)
    try:
        status, payload = _post(port, "/api/run-set", _DF)
        assert status == 200
        assert len(payload["jobIds"]) == 2
    finally:
        server.shutdown()
        server.server_close()

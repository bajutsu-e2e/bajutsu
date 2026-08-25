"""Tests for the per-scenario cloud-batch fan-out dispatch (BE-0336 Unit 3).

`start_run_set` expands one scenario-set request into one cloud-batch job per scenario, each carrying
its own `BatchRequest`, registered through the same concurrency-capped tail as every other run. A
recording executor captures the dispatched jobs so the fan-out is asserted without actually running
them (no thread, no cloud).
"""

from __future__ import annotations

from pathlib import Path

from _shared import _post, _serve

from bajutsu import serve as srv
from bajutsu.serve.operations.dispatch import start_run_set
from bajutsu.serve.state import Job, ServeState


class _RecordingExecutor:
    """Captures dispatched jobs instead of running them, so a fan-out test stays synchronous."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def dispatch(self, state: ServeState, job: Job) -> None:
        self.jobs.append(job)


def _android_batch_project(
    tmp_path: Path, *, scenarios: list[str], budget: int | None = None
) -> tuple[Path, Path]:
    """A scenarios dir + config for an Android target wired for cloud-batch runs. *budget* sets the
    target's `cloudBatchBudget` (the device budget K); None omits it (unbounded by config)."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    body = "- name: a\n  steps:\n    - tap: { id: x }\n"
    for name in scenarios:
        (scn_dir / name).write_text(body, encoding="utf-8")
    apk = tmp_path / "app.apk"
    apk.write_text("APK", encoding="utf-8")
    budget_line = f"    cloudBatchBudget: {budget}\n" if budget is not None else ""
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        "  demo:\n"
        "    platform: android\n"
        "    package: com.example.demo\n"
        f"    scenarios: {scn_dir}\n"
        "    cloudBatch: devicefarm\n"
        f"{budget_line}"
        f"    appPath: {apk}\n",
        encoding="utf-8",
    )
    return scn_dir, cfg


def _state(tmp_path: Path, cfg: Path, scn_dir: Path) -> tuple[ServeState, _RecordingExecutor]:
    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
    )
    return state, executor


def test_fan_out_registers_one_batch_job_per_scenario(tmp_path: Path) -> None:
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 200
    assert len(payload["jobIds"]) == 3
    # Every dispatched job carries a per-scenario BatchRequest — one scenario each, not the whole set.
    for job in executor.jobs:
        assert job.batch is not None
    scenarios = sorted(job.batch.scenario for job in executor.jobs if job.batch is not None)
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

    payload, status = start_run_set(state, {"target": "demo", "scenarios": ["two.yaml"]})

    assert status == 200
    assert len(payload["jobIds"]) == 1
    batch = executor.jobs[0].batch
    assert batch is not None and batch.scenario == "scenarios/two.yaml"


def test_fan_out_rejects_a_target_not_wired_for_cloud_batch(tmp_path: Path) -> None:
    # A target with no cloudBatch provider is a local target; the cloud fan-out surface refuses it
    # loudly rather than silently dispatching nothing.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "one.yaml").write_text(
        "- name: a\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        f"targets: {{ demo: {{ platform: android, package: com.example.demo, scenarios: {scn_dir} }} }}\n",
        encoding="utf-8",
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 400
    assert "cloudBatch" in payload["error"]
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
        "targets:\n"
        "  demo:\n"
        "    platform: web\n"
        "    baseUrl: http://localhost:8080\n"
        f"    scenarios: {scn_dir}\n"
        "    cloudBatch: devicefarm\n",
        encoding="utf-8",
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 400
    assert "android or ios" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_an_unknown_scenario_before_dispatching_any(tmp_path: Path) -> None:
    # A requested scenario that isn't in the target's dir fails the whole request closed — no partial
    # fan-out that dispatched the good ones and errored on the bad one.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(
        state, {"target": "demo", "scenarios": ["one.yaml", "ghost.yaml"]}
    )

    assert status == 400
    assert "ghost.yaml" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_a_target_with_no_app_to_install(tmp_path: Path) -> None:
    # A cloud-batch run installs an app on the reserved device; a target wired for cloudBatch but
    # with no appPath is refused loudly rather than submitting a run that can't install anything.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "one.yaml").write_text(
        "- name: a\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        "  demo:\n"
        "    platform: android\n"
        "    package: com.example.demo\n"
        f"    scenarios: {scn_dir}\n"
        "    cloudBatch: devicefarm\n",
        encoding="utf-8",
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 400
    assert "appPath" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_an_empty_scenario_subset(tmp_path: Path) -> None:
    # An explicit empty list has nothing to run; a spec that runs nothing would silently "pass", so
    # the request fails closed instead.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo", "scenarios": []})

    assert status == 400
    assert "no scenarios" in payload["error"]
    assert executor.jobs == []


def test_fan_out_rejects_a_config_outside_the_run_directory(tmp_path: Path) -> None:
    # The provider packages the run directory (state.cwd) at the package root, so a config living
    # outside it would travel as a `../…` path the cloud host can't find. Fail loud at the endpoint.
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
    )

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 400
    assert "cloud-batch package root" in payload["error"]
    assert executor.jobs == []


def test_fan_out_requires_an_open_config(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, executor=_RecordingExecutor())
    payload, status = start_run_set(state, {"target": "demo"})
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
    )
    with unittest.mock.patch.object(state, "try_register", return_value=None):
        payload, status = start_run_set(state, {"target": "demo"})

    assert status == 429
    assert "too many concurrent" in payload["error"]


def test_fan_out_stops_at_the_device_budget(tmp_path: Path) -> None:
    # The device budget K bounds how many of a target's cloud-batch runs reserve a device at once
    # (BE-0336 Unit 4). With K=2 and three scenarios, the fan-out registers two (each holding a
    # device) and stops at the third — a partial dispatch, the routine outcome the cap governs, not
    # an error. The recording executor never finishes its jobs, so both registered runs stay in
    # flight and the third hits the cap.
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"], budget=2
    )
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 200
    assert len(payload["jobIds"]) == 2
    assert len(executor.jobs) == 2


def test_request_can_lower_the_device_budget(tmp_path: Path) -> None:
    # A request may lower the target's budget (never raise it): with a config budget of 3 and a
    # per-request deviceBudget of 1, only one scenario is dispatched before the cap stops the fan-out.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"], budget=3)
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo", "deviceBudget": 1})

    assert status == 200
    assert len(payload["jobIds"]) == 1
    assert len(executor.jobs) == 1


def test_request_cannot_raise_the_device_budget(tmp_path: Path) -> None:
    # The per-request override only tightens: a deviceBudget above the config budget leaves the
    # config budget in force, so K=1 still stops the fan-out at one dispatched run.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"], budget=1)
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo", "deviceBudget": 5})

    assert status == 200
    assert len(payload["jobIds"]) == 1
    assert len(executor.jobs) == 1


def test_request_budget_bounds_a_target_with_no_config_budget(tmp_path: Path) -> None:
    # A target with no configured budget is unbounded by config; a per-request deviceBudget still
    # bounds that dispatch (it lowers from "unbounded"), stopping the fan-out at the requested count.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state, _ = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo", "deviceBudget": 1})

    assert status == 200
    assert len(payload["jobIds"]) == 1


def test_request_rejects_a_non_positive_device_budget(tmp_path: Path) -> None:
    # A resource cap the caller asked for must fail loudly, not silently evaporate into "unbounded":
    # a non-positive deviceBudget is a 400, and nothing is dispatched (BE-0336 Unit 4).
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo", "deviceBudget": 0})

    assert status == 400
    assert "deviceBudget" in payload["error"]
    assert executor.jobs == []


def test_request_rejects_a_malformed_device_budget(tmp_path: Path) -> None:
    # A non-integer deviceBudget (string, float, bool) is a client error, not a silent truncation to
    # "no request bound" — reject it with a 400 before any dispatch.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml"])
    state, executor = _state(tmp_path, cfg, scn_dir)

    for bad in ("two", 1.9, True):
        payload, status = start_run_set(state, {"target": "demo", "deviceBudget": bad})
        assert status == 400
        assert "deviceBudget" in payload["error"]
    assert executor.jobs == []


def test_fan_out_unbounded_without_a_budget(tmp_path: Path) -> None:
    # No config budget and no request override = the pre-Unit-4 behavior: every scenario dispatches.
    scn_dir, cfg = _android_batch_project(
        tmp_path, scenarios=["one.yaml", "two.yaml", "three.yaml"]
    )
    state, _ = _state(tmp_path, cfg, scn_dir)

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 200
    assert len(payload["jobIds"]) == 3


def test_fan_out_rejects_scenarios_that_travel_as_materials(tmp_path: Path) -> None:
    # The batch provider packages work_dir (the on-disk run directory) and names the scenario as a
    # relpath inside it. On the server-backed scenario store, runnable.arg is already workspace-
    # relative and runnable.materials is non-empty (the scenario text is shipped out-of-band). In
    # that case `os.path.relpath(arg, work_dir)` can produce a wrong result. Fail loud rather than
    # packaging a scenario path that won't exist inside the zip.
    from bajutsu.serve.scenarios import Runnable

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
        def scope(self, app):
            return _MaterialsScope()

    executor = _RecordingExecutor()
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        executor=executor,
    )
    # Inject the materials-returning scope via for_org().scenarios
    import unittest.mock

    mock_bundle = unittest.mock.MagicMock()
    mock_bundle.scenarios = _MaterialsStore()
    with unittest.mock.patch.object(state, "for_org", return_value=mock_bundle):
        payload, status = start_run_set(state, {"target": "demo"})

    assert status == 400
    assert "materials" in payload["error"].lower() or "on-disk" in payload["error"].lower()
    assert executor.jobs == []


def test_run_set_endpoint_is_wired(tmp_path: Path) -> None:
    # The POST /api/run-set route reaches start_run_set end to end through the real server: a
    # scenario-set request returns one job id per scenario. The async jobs then fail (no provider is
    # registered in this process), but that is downstream of the dispatch response asserted here.
    scn_dir, cfg = _android_batch_project(tmp_path, scenarios=["one.yaml", "two.yaml"])
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=tmp_path / "runs", cwd=tmp_path
    )
    server, port = _serve(state)
    try:
        status, payload = _post(port, "/api/run-set", {"target": "demo"})
        assert status == 200
        assert len(payload["jobIds"]) == 2
    finally:
        server.shutdown()
        server.server_close()

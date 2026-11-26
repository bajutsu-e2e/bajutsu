"""A `leased` worker borrows for the job it claimed, and declines what it cannot borrow (BE-0511)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer
from _lease_stub import events, lease_command

from bajutsu.common.runner.device_provider import LeaseEnvConflictError
from bajutsu.common.runner.device_provider.lease_command import LeaseDeclinedError
from bajutsu.run import INTERNAL_MODULE
from bajutsu.run.demand import run_demand
from bajutsu.serve import InMemoryLogBus
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.cli.worker_devices import JobDevices
from bajutsu.serve.server import worker_job
from bajutsu.serve.state import Job

_RUN = ["python", "-m", INTERNAL_MODULE, "--target", "ios"]


def _worker_file(tmp_path: Path, mode: str = "ok", extra: str = "") -> tuple[Path, Path]:
    command, log = lease_command(tmp_path, mode)
    path = tmp_path / "worker.yaml"
    path.write_text(
        "version: 1\nenvironment: leased\ndrivers: [xcuitest, adb, playwright, fake]\n"
        f"platforms: [ios, android]\nlease:\n  command: {json.dumps(command)}\n{extra}",
        encoding="utf-8",
    )
    return path, log


def _pools(*pools: tuple[str, int, list[str]]) -> list[dict[str, Any]]:
    return [{"platform": p, "count": n, "models": m} for p, n, m in pools]


def _devices(
    worker_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    pools: list[dict[str, Any]],
    *,
    workers: int = 1,
    stop: Callable[[], bool] = lambda: False,
    budget: float = 60.0,
) -> tuple[JobDevices, Job, list[str]]:
    lines: list[str] = []
    devices = JobDevices(
        worker_file=worker_file,
        cwd=worker_file.parent,
        stop=stop,
        log=lines.append,
        budget=budget,
    )
    monkeypatch.setattr(devices, "_demand", lambda _job, _flags: (pools, workers))
    return devices, Job(id="j1", cmd=list(_RUN)), lines


def _ended(log: Path) -> int:
    return len([e for e in events(log) if e.startswith("terminated")])


def _asked(log: Path) -> list[str]:
    return [e.removeprefix("argv ") for e in events(log) if e.startswith("argv ")]


def test_the_run_gets_every_borrowed_device_tagged_by_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, log = _worker_file(tmp_path, "limit:3")
    pools = _pools(("ios", 2, ["iPhone 16"]), ("android", 1, []))
    devices, job, _ = _devices(worker_file, monkeypatch, pools)
    assert devices.prepare(job)
    assert job.cmd[-2:] == ["--udid", "ios=ios-0,ios=ios-1,android=android-2"]
    assert job.lease_env == {"STUB_PORT": "5038"}
    assert _asked(log) == [
        "--platform ios --device iPhone 16",
        "--platform ios --device iPhone 16",
        "--platform android",
    ]
    assert _ended(log) == 0
    devices.release()
    assert _ended(log) == 3


def test_a_shortfall_of_the_mandatory_demand_holds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, log = _worker_file(tmp_path, "limit:1")
    devices, job, _ = _devices(worker_file, monkeypatch, _pools(("ios", 2, [])))
    with pytest.raises(LeaseDeclinedError, match="pool exhausted"):
        devices.prepare(job)
    assert _ended(log) == 1
    assert "--udid" not in job.cmd


def test_extra_lanes_are_whole_and_a_shortfall_never_declines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Five devices lend two lanes of two; the third lane gets one and gives it back.
    worker_file, log = _worker_file(tmp_path, "limit:5")
    pools = _pools(("ios", 1, []), ("android", 1, []))
    devices, job, lines = _devices(worker_file, monkeypatch, pools, workers=4)
    assert devices.prepare(job)
    assert job.cmd[-1] == "ios=ios-0,android=android-1,ios=ios-2,android=android-3"
    assert _ended(log) == 1
    assert any(line.startswith("running 2 lane(s) of 4") for line in lines)
    devices.release()
    assert _ended(log) == 5


def test_a_list_of_models_takes_the_count_for_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, log = _worker_file(tmp_path, "limit:2")
    devices, job, _ = _devices(worker_file, monkeypatch, _pools(("ios", 1, ["A", "B"])))
    assert devices.prepare(job)
    assert _asked(log) == ["--platform ios --device A", "--platform ios --device B"]
    devices.release()


def test_conflicting_variables_fail_the_job_with_the_key_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, log = _worker_file(tmp_path, "per-device-env")
    devices, job, _ = _devices(worker_file, monkeypatch, _pools(("ios", 2, [])))
    with pytest.raises(LeaseEnvConflictError, match="STUB_PORT"):
        devices.prepare(job)
    assert _ended(log) == 2


def test_a_job_lost_between_borrows_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, log = _worker_file(tmp_path, "limit:2")
    asked: list[None] = []

    def stop() -> bool:
        asked.append(None)
        return len(asked) > 2  # before the job, before the first borrow, then up

    devices, job, _ = _devices(worker_file, monkeypatch, _pools(("ios", 2, [])), stop=stop)
    assert not devices.prepare(job)
    assert (_ended(log), "--udid" in job.cmd) == (1, False)


def test_a_spent_budget_declines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker_file, log = _worker_file(tmp_path)
    devices, job, _ = _devices(worker_file, monkeypatch, _pools(("ios", 1, [])), budget=0.0)
    with pytest.raises(LeaseDeclinedError, match="borrow budget"):
        devices.prepare(job)
    assert events(log) == []


def test_only_a_run_borrows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker_file, log = _worker_file(tmp_path)
    devices, _, _ = _devices(worker_file, monkeypatch, _pools(("ios", 1, [])))
    assert devices.prepare(Job(id="j", cmd=["python", "-m", "bajutsu", "record"]))
    assert events(log) == []


def test_a_leased_worker_drops_a_requested_udid(tmp_path: Path) -> None:
    worker_file, _ = _worker_file(tmp_path)
    seen: list[Job] = []

    def prepare(job: Job) -> bool:
        seen.append(job)
        return False

    spec = {"job_id": "j1", "cmd": [*_RUN, "--udid", "AAAA", "--workers", "2"], "udids": ["AAAA"]}
    worker_job.execute_job_spec(spec, cwd=tmp_path, worker_config=worker_file, prepare=prepare)
    (job,) = seen
    assert "--udid" not in job.cmd
    assert job.cmd[-2:] == ["--workers", "2"]
    assert (job.udids, job.exit_code) == ([], None)


def test_a_declined_job_is_posted_back_to_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, _ = _worker_file(tmp_path, "decline")
    monkeypatch.setattr(JobDevices, "_demand", lambda *_a: (_pools(("ios", 1, [])), 1))
    posts: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, body: dict[str, Any], **_kwargs: Any) -> tuple[int, Any]:
        posts.append((url, body))
        return 200, {"status": "queued"}

    monkeypatch.setattr(worker_mod, "_post_json", post)
    outcome = worker_mod._run_with_heartbeat(
        {"job_id": "j1", "cmd": list(_RUN)},
        job_id="j1",
        work=tmp_path,
        bundle_urls=None,
        bus=InMemoryLogBus(),
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=5,
        worker_config=worker_file,
        environment="leased",
        lost=threading.Event(),
    )
    worker_mod._finish_job(
        url="http://cp",
        auth_token=None,
        wid="w1",
        job_id="j1",
        spec={},
        outcome=outcome,
    )
    ((url, body),) = posts
    assert url == "http://cp/api/worker/decline"
    assert (body["job_id"], body["worker_id"], body["environment"]) == ("j1", "w1", "leased")
    assert "no device free" in body["reason"]


# --- what a run would borrow ---------------------------------------------------------------

_CONFIG = """
targets:
  app: { platform: ios, subject: { id: com.x }, runsOn: { ios: { model: "iPhone 16" } } }
  peer: { platform: ios, subject: { id: com.y } }
  pad: { platform: ios, subject: { id: com.z }, runsOn: { ios: { family: ipad } } }
  droid: { platform: android, subject: { id: com.x } }
  site: { platform: web, subject: { url: "http://localhost:1" } }
"""


def _demand(tmp_path: Path, scenarios: str, *flags: str) -> dict[str, Any]:
    (tmp_path / "bajutsu.config.yaml").write_text(_CONFIG, encoding="utf-8")
    (tmp_path / "s.yaml").write_text(scenarios, encoding="utf-8")
    worker_file, _ = _worker_file(tmp_path)
    return run_demand(
        [
            "--config",
            str(tmp_path / "bajutsu.config.yaml"),
            "--scenario",
            str(tmp_path / "s.yaml"),
            "--worker-config",
            str(worker_file),
            *flags,
        ]
    )


def _stub_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve each platform's actuator without this host's device toolchains."""
    actuators = {"ios": "xcuitest", "android": "adb", "web": "playwright"}
    monkeypatch.setattr("bajutsu.run.cli.select_actuator", lambda b: actuators[b[0]])
    monkeypatch.setattr("bajutsu.run.cli._worker_backends", lambda backends, _worker: backends)
    monkeypatch.setattr("bajutsu.run.cli._worker_capability_failures", lambda *_a: {})


def test_demand_counts_each_platform_pool_and_leaves_the_web_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_host(monkeypatch)
    scenarios = (
        "- name: cross\n  targets: [app, peer, droid, site]\n  steps: []\n"
        "- name: solo\n  targets: [app]\n  steps: []\n"
    )
    answer = _demand(tmp_path, scenarios, "--workers", "3")
    assert answer == {
        "pools": [
            {"platform": "ios", "count": 2, "models": ["iPhone 16"], "unnamed": 0},
            {"platform": "android", "count": 1, "models": [], "unnamed": 0},
        ],
        "workers": 3,
    }


def test_demand_gives_a_targetless_scenario_one_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_host(monkeypatch)
    answer = _demand(tmp_path, "- name: solo\n  steps: []\n", "--target", "app")
    assert answer["pools"] == [
        {"platform": "ios", "count": 1, "models": ["iPhone 16"], "unnamed": 0}
    ]


# --- the device count the worker asks for ---------------------------------------------------


def test_the_worker_counts_devices_in_the_jobs_directory(tmp_path: Path) -> None:
    (tmp_path / "bajutsu.config.yaml").write_text(
        "targets:\n  app: { platform: ios, subject: { id: com.x } }\n", encoding="utf-8"
    )
    (tmp_path / "s.yaml").write_text("- name: solo\n  steps: []\n", encoding="utf-8")
    worker_file, log = _worker_file(tmp_path)
    devices = JobDevices(
        worker_file=worker_file, cwd=tmp_path, stop=lambda: False, log=lambda _line: None
    )
    # Relative paths, as a leased job's argv carries them; `--backend fake` needs no toolchain.
    flags = ["--config", "bajutsu.config.yaml", "--scenario", "s.yaml", "--target", "app"]
    job = Job(
        id="j1", cmd=[*_RUN[:3], *flags, "--backend", "fake", "--worker-config", str(worker_file)]
    )
    assert devices.prepare(job)
    assert events(log) == []  # the fake backend drives no device, so nothing is borrowed
    assert "--udid" not in job.cmd


def test_devices_that_cannot_be_counted_fail_the_job_with_the_runs_message(
    tmp_path: Path,
) -> None:
    worker_file, log = _worker_file(tmp_path)
    devices = JobDevices(
        worker_file=worker_file, cwd=tmp_path, stop=lambda: False, log=lambda _line: None
    )
    job = Job(id="j1", cmd=[*_RUN, "--config", "absent.yaml", "--scenario", "absent.yaml"])
    with pytest.raises(ValueError, match="could not count the devices"):
        devices.prepare(job)
    assert events(log) == []


def test_a_job_already_cancelled_borrows_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, log = _worker_file(tmp_path)
    devices, job, _ = _devices(worker_file, monkeypatch, _pools(("ios", 1, [])), stop=lambda: True)
    assert not devices.prepare(job)
    assert events(log) == []


def test_a_stop_during_the_extra_lanes_releases_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, log = _worker_file(tmp_path, "limit:3")
    asked: list[None] = []

    def stop() -> bool:
        asked.append(None)
        return len(asked) > 2  # before the job and before the mandatory borrow

    devices, job, _ = _devices(
        worker_file, monkeypatch, _pools(("ios", 1, [])), workers=3, stop=stop
    )
    assert not devices.prepare(job)
    assert _ended(log) == 1


def test_demand_prints_one_json_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from bajutsu.run import demand

    monkeypatch.setattr("bajutsu.cli.bootstrap", lambda **_kwargs: None)
    monkeypatch.setattr(demand, "run_demand", lambda _args: {"pools": [], "workers": 1})
    demand.main()
    assert json.loads(capsys.readouterr().out) == {"pools": [], "workers": 1}

    def refuse(_args: list[str]) -> dict[str, Any]:
        raise typer.Exit(2)

    monkeypatch.setattr(demand, "run_demand", refuse)
    with pytest.raises(SystemExit) as stopped:
        demand.main()
    assert stopped.value.code == 2


def test_a_condition_the_named_models_may_miss_gets_one_more_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_host(monkeypatch)
    scenarios = (
        "- name: named\n  targets: [app]\n  steps: []\n- name: any\n  targets: [pad]\n  steps: []\n"
    )
    answer = _demand(tmp_path, scenarios)
    assert answer["pools"] == [
        {"platform": "ios", "count": 1, "models": ["iPhone 16"], "unnamed": 1}
    ]


def test_a_lease_lost_mid_run_is_named_in_the_job_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, _ = _worker_file(tmp_path, "dies")
    devices, job, lines = _devices(worker_file, monkeypatch, _pools(("ios", 1, [])))
    assert devices.prepare(job)
    for _ in range(200):
        if devices._held.devices[0][1].problem() is not None:
            break
        threading.Event().wait(0.05)
    devices.report_problems()
    devices.release()
    assert any("ended before the run released it" in line for line in lines)


def test_a_nearly_spent_budget_is_named_in_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, _ = _worker_file(tmp_path, "decline")
    devices, job, _ = _devices(worker_file, monkeypatch, _pools(("ios", 1, [])), budget=20.0)
    with pytest.raises(LeaseDeclinedError, match="of the 20 s borrow budget"):
        devices.prepare(job)


@pytest.mark.parametrize(("code", "tries"), [(503, 3), (404, 1), (None, 3)])
def test_a_decline_that_does_not_land_is_retried(
    monkeypatch: pytest.MonkeyPatch, code: int | None, tries: int
) -> None:
    posts: list[str] = []

    def post(url: str, _body: dict[str, Any], **_kwargs: Any) -> tuple[int, Any]:
        posts.append(url)
        if code is None:
            raise OSError("connection reset")
        return code, {}

    monkeypatch.setattr(worker_mod, "_post_json", post)
    monkeypatch.setattr(worker_mod, "_DECLINE_RETRY_SECONDS", 0)
    declined = {"declined": "busy", "environment": "leased"}
    worker_mod._post_decline("http://cp", "j1", "w1", declined, None)
    assert len(posts) == tries


def test_a_dropped_request_udid_is_said_in_the_job_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_file, _ = _worker_file(tmp_path)
    bus = InMemoryLogBus()
    published: list[str] = []
    monkeypatch.setattr(bus, "publish", lambda _job, line: published.append(line))
    worker_job.execute_job_spec(
        {"job_id": "j1", "cmd": [*_RUN, "--udid", "AAAA"]},
        cwd=tmp_path,
        bus=bus,
        worker_config=worker_file,
        prepare=lambda _job: False,
    )
    assert any("ignoring the requested device" in line for line in published)


def test_a_cancel_during_the_borrow_records_the_job_cancelled(tmp_path: Path) -> None:
    class _Devices:
        def prepare(self, _job: Job) -> bool:
            return False

        def release(self) -> None:
            pass

    cancel = worker_job.CancelRelay()
    cancel.request()
    result = worker_mod._job_result(
        lambda **_kwargs: Job(id="j1", cmd=list(_RUN)),
        _Devices(),  # type: ignore[arg-type]
        job_id="j1",
        environment="leased",
        cancel=cancel,
        log=lambda _line: None,
    )
    assert result["cancelled"] is True


def test_an_unnamed_target_adds_one_device_per_held_target_not_per_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_host(monkeypatch)
    (pool,) = _demand(tmp_path, "- name: both\n  targets: [app, pad]\n  steps: []\n")["pools"]
    assert (pool["count"], pool["models"], pool["unnamed"]) == (2, ["iPhone 16"], 1)
    worker_file, log = _worker_file(tmp_path, "limit:9")
    devices, job, _ = _devices(worker_file, monkeypatch, [pool])
    assert devices.prepare(job)
    assert _asked(log) == [
        "--platform ios --device iPhone 16",
        "--platform ios --device iPhone 16",
        "--platform ios",
    ]
    devices.release()


def test_a_409_after_an_unanswered_decline_is_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    answers: list[Any] = [OSError("reset"), (409, {})]

    def post(_url: str, _body: dict[str, Any], **_kwargs: Any) -> Any:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(worker_mod, "_post_json", post)
    monkeypatch.setattr(worker_mod, "_DECLINE_RETRY_SECONDS", 0)
    declined = {"declined": "busy", "environment": "leased"}
    worker_mod._post_decline("http://cp", "j1", "w1", declined, None)
    assert answers == []
    assert "could not decline" not in caplog.text


def test_a_lost_lease_is_in_the_job_log_before_it_uploads(tmp_path: Path) -> None:
    reported: list[None] = []

    class _Devices:
        def report_problems(self) -> None:
            reported.append(None)

    devices: Any = _Devices()
    worker_mod._finish_job_log(tmp_path, InMemoryLogBus(), devices, Job(id="j1", cmd=list(_RUN)))
    assert reported == [None]

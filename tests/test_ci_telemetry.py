"""Tests for `scripts/ci_telemetry.py`, the per-job CI telemetry record and its cross-run report.

The record is only worth comparing across runs if its numbers mean the same thing on every job, so
the parsers are pinned against hand-built samples of each sampler's real output. The log-signal
patterns are pinned against the warning templates in `bajutsu` itself: a reworded warning would
otherwise count zero forever and read as a healthy channel.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts import ci_telemetry as ct

REPO = Path(__file__).resolve().parent.parent

MAC_TELEMETRY = """\
=== 2026-11-20T01:00:00Z ===
Load Avg: 3.05, 2.89, 2.70
CPU usage: 12.5% user, 8.33% sys, 79.16% idle
Disks: 123456/4567M read, 789/123M written.
System-wide memory free percentage: 23%
Interval CPU usage: 50.0% user, 46.0% sys, 4.0% idle
=== 2026-11-20T01:00:20Z ===
Load Avg: 9.5, 2.89, 2.70
Disks: 123999/5G read, 789/200M written.
System-wide memory free percentage: 8%
"""

LINUX_TELEMETRY = """\
2026-11-20T01:00:00Z
top - 01:00:00 up 5 min,  0 users,  load average: 2.10, 1.50, 0.80
%Cpu(s): 10.0 us,  3.0 sy,  0.0 ni, 85.0 id,  2.0 wa,  0.0 hi,  0.0 si,  0.0 st
               total        used        free      shared  buff/cache   available
Mem:           16000        9000        1000         100        6000        4000
Interval %Cpu(s): 60.0 us, 10.0 sy,  0.0 ni, 3.0 id, 25.0 wa,  0.0 hi,  0.0 si, 12.0 st
"""


def test_parses_the_macos_sampler() -> None:
    load = ct.parse_host_telemetry(MAC_TELEMETRY)
    assert load["samples"] == 2
    assert load["load1_max"] == 9.5
    # The interval line wins over the since-boot single sample.
    assert load["cpu_idle_min"] == 4.0
    assert load["cpu_idle_measured"] is True
    assert load["mem_free_min_pct"] == 8.0
    assert load["disk_mb_per_sample_max"] == pytest.approx(630.0)


def test_parses_the_linux_sampler() -> None:
    load = ct.parse_host_telemetry(LINUX_TELEMETRY)
    assert load["samples"] == 1
    assert load["load1_max"] == 2.1
    assert load["cpu_idle_min"] == 3.0
    assert load["iowait_max"] == 25.0
    assert load["steal_max"] == 12.0
    assert load["mem_free_min_pct"] == pytest.approx(25.0)


def test_since_boot_iowait_and_steal_give_way_to_the_interval_reading() -> None:
    text = (
        "%Cpu(s): 10.0 us, 3.0 sy, 0.0 ni, 40.0 id, 45.0 wa, 0.0 hi, 0.0 si, 30.0 st\n"
        "Interval %Cpu(s): 10.0 us, 3.0 sy, 0.0 ni, 80.0 id, 1.0 wa, 0.0 hi, 0.0 si, 2.0 st\n"
    )
    load = ct.parse_host_telemetry(text)
    assert (load["iowait_max"], load["steal_max"]) == (1.0, 2.0)


def test_single_sample_cpu_is_used_only_without_an_interval_reading() -> None:
    load = ct.parse_host_telemetry("CPU usage: 1% user, 1% sys, 98.0% idle\n")
    assert load["cpu_idle_min"] == 98.0
    assert load["cpu_idle_measured"] is False


def test_empty_telemetry_yields_no_numbers() -> None:
    load = ct.parse_host_telemetry("")
    assert load["samples"] == 0
    assert load["load1_max"] is None
    assert load["disk_mb_per_sample_max"] is None


def test_size_units() -> None:
    assert ct._size_mb("2G") == 2048
    assert ct._size_mb("512K") == 0.5
    assert ct._size_mb("garbage") == 0.0


def test_render_probe() -> None:
    probe = ct.parse_render_probe(
        "screenshot: exit 0 in 2s, 1234 bytes\nrecordVideo: exit 0, 0 bytes in 5s\n"
    )
    assert probe == {
        "screenshot_s": 2.0,
        "screenshot_hung": False,
        "screenshot_bytes": 1234,
        "video_bytes": 0,
    }
    hung = ct.parse_render_probe("screenshot: STILL RUNNING after 15s — killed\n")
    assert hung["screenshot_hung"] is True
    assert hung["screenshot_s"] == 15.0


def test_host_environment() -> None:
    text = (
        "ProductVersion:\t15.6\nBuildVersion:\t24G84\nhw.model: Mac14,2\nhw.ncpu: 3\n"
        "hw.memsize: 7516192768\nkern.hv_vmm_present: 1\nXcode 26.1\nBuild version 17B55\n"
    )
    assert ct.parse_host_environment(text) == {
        "os_version": "15.6",
        "os_build": "24G84",
        "hw_model": "Mac14,2",
        "ncpu": 3,
        "memory_gb": 7,
        "virtualized": True,
        "xcode": "26.1",
        "xcode_build": "17B55",
    }


def _manifest(ok: bool, failed_action: str = "wait") -> dict[str, Any]:
    steps: list[dict[str, Any]] = [
        {"action": "launch", "ok": True, "duration_s": 3.0},
        {"action": "wait", "ok": True, "duration_s": 1.5},
    ]
    if not ok:
        steps.append({"action": failed_action, "ok": False, "duration_s": 20.0})
    return {
        "scenarios": [
            {
                "scenario": "navigation",
                "ok": ok,
                "failure": None if ok else "step 2: failed",
                "steps": steps,
            }
        ]
    }


def test_summarize_manifest_separates_the_first_wait() -> None:
    (row,) = ct.summarize_manifest(_manifest(ok=False))
    assert row["first_wait_s"] == 1.5
    assert row["max_wait_s"] == 20.0
    assert row["failed_action"] == "wait"
    assert row["duration_s"] == 24.5


def _runs(tmp_path: Path, *, ok: bool = True, failed_action: str = "wait") -> Path:
    runs = tmp_path / "runs"
    diagnostics = runs / "diagnostics"
    (diagnostics / "stalls" / "stall-01-video-no-bytes-4242").mkdir(parents=True)
    (diagnostics / "host-telemetry.log").write_text(MAC_TELEMETRY)
    (diagnostics / "render-probe.txt").write_text("screenshot: exit 0 in 1s, 10 bytes\n")
    (diagnostics / "host-environment.txt").write_text("hw.ncpu: 3\nXcode 26.1\n")
    run = runs / "20260101-000000"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(_manifest(ok, failed_action)))
    return runs


def test_summarize_and_write(tmp_path: Path) -> None:
    runs = _runs(tmp_path, ok=False)
    env = {"GITHUB_JOB": "actuation", "ImageVersion": "20261110.1", "RUNNER_NAME": "r1"}
    record = ct.summarize(runs, "ios", env=env)
    assert record["ci"]["job"] == "actuation"
    assert record["ci"]["image_version"] == "20261110.1"
    assert record["stalls"] == {"video-no-bytes": 1}
    assert record["runs_ok"] is False
    assert record["scenarios"][0]["run"] == "20260101-000000"
    out = tmp_path / "out"
    path = ct.write_summary(record, runs, out)
    assert json.loads(path.read_text())["lane"] == "ios"
    assert (out / "manifests" / "20260101-000000" / "manifest.json").is_file()
    # A second summarize over a `runs/` that now holds the first copy must not count it twice.
    out_in_runs = runs / "diagnostics" / "ci-telemetry"
    ct.write_summary(record, runs, out_in_runs)
    assert len(ct.summarize(runs, "ios", env=env)["scenarios"]) == 1


def test_android_record_reads_the_linux_host(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    record = ct.summarize(runs, "android", env={})
    assert record["render_probe"] is None
    assert record["runs_ok"] is None
    assert record["host"]["ncpu"]


def test_candidate_causes_flag_each_infrastructure_signal() -> None:
    record: dict[str, Any] = {
        "host": {"ncpu": 3},
        "host_load": {"load1_max": 9.5},
        "render_probe": {"screenshot_hung": True},
        "log_signals": {"channel_retries": 2, "video_start_unconfirmed": 1, "network_errors": 0},
        "api": {"queue_s": 900, "failed_step": "Set up the iOS toolchain"},
        "scenarios": [{"ok": False, "failed_action": "wait"}],
    }
    assert ct.candidate_causes(record) == [
        "host-resources",
        "device-slow-or-wedged",
        "channel-timeout",
        "video-start",
        "network-or-deps",
        "runner-contention",
    ]


def test_a_failure_with_no_infrastructure_signal_is_residual() -> None:
    wait = {"scenarios": [{"ok": False, "failed_action": "wait"}]}
    assert ct.candidate_causes(wait) == ["app-wait-timeout"]
    assertion = {"scenarios": [{"ok": False, "failed_action": "tap"}]}
    assert ct.candidate_causes(assertion) == ["test-assertion"]
    assert ct.candidate_causes({"scenarios": [{"ok": True}]}) == []


def test_a_fetched_failure_without_log_signals_is_not_residual() -> None:
    record: dict[str, Any] = {
        "api": {"conclusion": "failure"},
        "log_signals": None,
        "scenarios": [{"ok": False, "failed_action": "wait"}],
    }
    assert ct.candidate_causes(record) == ["log-signals-unknown"]
    record["log_signals"] = {}
    assert ct.candidate_causes(record) == ["app-wait-timeout"]
    summary = ct.aggregate([{**record, "log_signals": None, "causes": ["log-signals-unknown"]}])
    assert len(summary["unexplained"]) == 1


def test_the_toolchain_step_is_not_a_device_boot() -> None:
    facts = ct.job_facts(
        {
            "steps": [
                {
                    "name": "Set up the iOS toolchain",
                    "conclusion": "success",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:10:00Z",
                }
            ]
        }
    )
    assert not facts["device_boot_s"]


def test_hour_bucket_prefers_the_job_start() -> None:
    record = {"hour_utc": 9, "api": {"started_at": "2026-01-01T07:50:00Z"}}
    assert ct._hour_bucket(record) == "04-07h UTC"


def test_a_failing_run_step_is_not_a_dependency_failure() -> None:
    record = {"api": {"failed_step": "Run scenarios"}, "scenarios": []}
    assert ct.candidate_causes(record) == []


# The template each LOG_SIGNALS entry counts, the file it lives in, and one rendered instance.
_LOG_SOURCES = [
    (
        "channel_retries",
        "bajutsu/common/drivers/xcuitest/_functions.py",
        '"runner channel %s %s failed (attempt %d/%d), retrying: %s"',
        "runner channel GET /elements failed (attempt 1/3), retrying: timed out",
    ),
    (
        "channel_crashes",
        "bajutsu/common/drivers/xcuitest/_functions.py",
        "the runner became unreachable past the retry budget",
        "runner channel GET /elements: the runner became unreachable past the retry budget — x",
    ),
    (
        "video_start_unconfirmed",
        "bajutsu/common/evidence/intervals/_functions.py",
        '"recordVideo did not report %r for %s within %ss; %s"',
        "recordVideo did not report 'Recording started' for /tmp/v.mp4 within 5.0s; x",
    ),
    (
        "video_start_unconfirmed",
        "bajutsu/common/evidence/intervals/_functions.py",
        '"screenrecord on %s wrote no bytes within %ss;',
        "screenrecord on emulator-5554 wrote no bytes within 5.0s; its start is confirmed",
    ),
]


@pytest.mark.parametrize(("signal", "source", "template", "rendered"), _LOG_SOURCES)
def test_log_signals_track_the_warnings_bajutsu_writes(
    signal: str, source: str, template: str, rendered: str
) -> None:
    assert template in (REPO / source).read_text(encoding="utf-8"), (
        f"{source} no longer logs {template!r}; update ci_telemetry.LOG_SIGNALS[{signal!r}]"
    )
    assert ct.count_log_signals(f"2026-11-20T01:00:00Z WARNING {rendered}\n")[signal] == 1


def test_job_facts_and_concurrency() -> None:
    job = {
        "id": 7,
        "name": "actuation (xcuitest)",
        "conclusion": "failure",
        "runner_name": "GitHub Actions 3",
        "created_at": "2026-11-20T01:00:00Z",
        "started_at": "2026-11-20T01:02:00Z",
        "completed_at": "2026-11-20T01:30:00Z",
        "steps": [
            {
                "name": "Wait for the Simulator",
                "conclusion": "success",
                "started_at": "2026-11-20T01:03:00Z",
                "completed_at": "2026-11-20T01:08:00Z",
            },
            {
                "name": "Run the actuation scenarios",
                "conclusion": "failure",
                "started_at": "2026-11-20T01:08:00Z",
                "completed_at": "2026-11-20T01:30:00Z",
            },
        ],
    }
    facts = ct.job_facts(job)
    assert facts["queue_s"] == 120
    assert facts["device_boot_s"] == 300
    assert facts["failed_step"] == "Run the actuation scenarios"
    other = {"started_at": "2026-11-20T01:00:00Z", "completed_at": "2026-11-20T01:05:00Z"}
    assert ct.concurrency([facts, other]) == [1, 0]
    assert ct._seconds("bad", "2026-11-20T01:00:00Z") is None


class _FakeGh:
    """Answers the four `gh` calls `fetch` makes from canned data, recording each."""

    def __init__(self, record: dict[str, Any], manifest: dict[str, Any]) -> None:
        self.record = record
        self.manifest = manifest
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["run", "list"]:
            return json.dumps([{"databaseId": 11, "event": "pull_request", "headBranch": "b"}])
        if args[:2] == ["run", "download"]:
            target = Path(args[args.index("--dir") + 1]) / "ci-telemetry-actuation-1"
            (target / "manifests" / "20260101-000000").mkdir(parents=True)
            (target / ct.RECORD_NAME).write_text(json.dumps(self.record))
            (target / "manifests" / "20260101-000000" / "manifest.json").write_text(
                json.dumps(self.manifest)
            )
            return ""
        if args[0] == "api" and args[-1] == ".jobs[] | @json":
            jobs = [
                {"id": 1, "name": "actuation", "conclusion": "failure", "runner_name": "r1"},
                {"id": 2, "name": "golden", "conclusion": "success", "runner_name": "r2"},
                {"id": 3, "name": "visual", "conclusion": "skipped", "runner_name": None},
                {"id": 4, "name": "network", "conclusion": "cancelled", "runner_name": "r4"},
                {"id": 5, "name": "device-group", "conclusion": "cancelled", "runner_name": "r5"},
            ]
            return "\n".join(json.dumps(j) for j in jobs) + "\n"
        if args[0] == "api" and args[1].endswith("/annotations"):
            if args[1].endswith("/5/annotations"):
                return "The job has exceeded the maximum execution time of 60 minutes.\n"
            return ""
        if args[0] == "api" and args[-1].endswith("/logs"):
            if args[-1].endswith("/2/logs"):
                raise subprocess.CalledProcessError(1, "gh")
            return "runner channel GET /elements failed (attempt 1/3), retrying: x\n"
        raise AssertionError(f"unexpected gh call: {args}")


def test_fetch_joins_records_with_the_api(tmp_path: Path) -> None:
    record = ct.summarize(_runs(tmp_path, ok=False), "ios", env={"RUNNER_NAME": "r1"})
    gh = _FakeGh(record, _manifest(ok=False))
    out = tmp_path / "fetched"
    assert ct.fetch("o/r", "ios-e2e.yml", 5, out, gh=gh) == 3
    records = ct.load_records(out)
    by_job = {r["api"]["job_name"]: r for r in records}
    # A superseded job is left out; one its timeout killed counts red.
    assert "network" not in by_job
    assert by_job["device-group"]["api"]["conclusion"] == "timed_out"
    assert ct.is_failed(by_job["device-group"])
    assert by_job["actuation"]["log_signals"]["channel_retries"] == 1
    assert "channel-timeout" in by_job["actuation"]["causes"]
    assert by_job["actuation"]["scenarios"]  # joined with the uploaded record
    # A job with no record still counts, and a withheld log is unknown rather than zero.
    assert by_job["golden"]["log_signals"] is None
    assert by_job["golden"]["ci"]["run_id"] == "11"
    assert (out / "manifests" / "11-r1-20260101-000000" / "manifest.json").is_file()


def test_aggregate_and_render(tmp_path: Path) -> None:
    red = {
        "api": {
            "conclusion": "failure",
            "job_name": "actuation",
            "started_at": "2026-11-20T05:00:00Z",
        },
        "ci": {"image_version": "img-1", "run_id": "1"},
        "host": {"hw_model": "Mac14,2"},
        "causes": ["video-start"],
        "scenarios": [{"first_wait_s": 20.0}],
    }
    green = {
        "api": {"conclusion": "success", "job_name": "actuation"},
        "hour_utc": 6,
        "ci": {"image_version": "img-1"},
        "causes": ["video-start"],
        "scenarios": [{"first_wait_s": 1.0}],
    }
    unexplained = {"api": {"conclusion": "failure", "job_name": "golden", "failed_step": "x"}}
    summary = ct.aggregate([red, green, unexplained])
    assert summary["red"] == 2
    assert summary["causes"]["video-start"] == {"red": 1, "green": 1}
    assert summary["dimensions"]["runner image"]["img-1"] == {"jobs": 2, "red": 1}
    assert summary["dimensions"]["hour"]["04-07h UTC"] == {"jobs": 2, "red": 1}
    assert summary["metrics"]["first_wait_s_max"] == {"red": 20.0, "green": 1.0}
    assert summary["unexplained"] == [{"job": "golden", "run_id": None, "failed_step": "x"}]
    (tmp_path / "manifests").mkdir()
    text = ct.render(summary, tmp_path)
    assert "| video-start | 1 | 1 |" in text
    assert "bajutsu flakiness --history" in text
    assert "Red jobs with no candidate cause" in text


def test_is_failed_falls_back_to_the_runs_verdict() -> None:
    assert ct.is_failed({"runs_ok": False}) is True
    assert ct.is_failed({"runs_ok": True}) is False
    assert ct.is_failed({"api": {"conclusion": "cancelled"}}) is True


def test_cli_summarize_then_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _runs(tmp_path, ok=False, failed_action="tap")
    monkeypatch.setenv("GITHUB_JOB", "run")
    out = tmp_path / "out"
    assert ct.main(["summarize", "--lane", "ios", "--runs", str(runs), "--out", str(out)]) == 0
    assert ct.main(["report", str(out)]) == 0
    text = capsys.readouterr().out
    assert "1 red of 1 jobs" in text
    assert ct.main(["report", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    # Load 9.5 on 3 CPUs: the host signal fires, so the failure is not called a test defect.
    assert summary["causes"] == {
        "host-resources": {"red": 1, "green": 0},
        "video-start": {"red": 1, "green": 0},
    }


def test_fetch_does_not_join_a_runner_name_two_jobs_share(tmp_path: Path) -> None:
    record = ct.summarize(_runs(tmp_path, ok=False), "ios", env={"RUNNER_NAME": "r1"})
    gh = _FakeGh(record, _manifest(ok=False))
    original = gh.__call__

    def shared(args: Sequence[str]) -> str:
        out = original(args)
        if list(args)[-1] == ".jobs[] | @json":
            out = out.replace('"runner_name": "r2"', '"runner_name": "r1"')
        return out

    out = tmp_path / "fetched"
    ct.fetch("o/r", "ios-e2e.yml", 5, out, gh=shared, with_logs=False)
    assert all("scenarios" not in r for r in ct.load_records(out))


def test_summarize_reads_an_explicit_diagnostics_dir(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    moved = tmp_path / "elsewhere"
    (runs / "diagnostics").rename(moved)
    record = ct.summarize(runs, "ios", env={}, diagnostics=moved)
    assert record["host"]["ncpu"] == 3
    assert record["host_load"]["samples"] == 2

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
import yaml

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


def test_the_cpu_ranked_rows_leave_every_reading_unchanged() -> None:
    # The sampler prints the two-sample top's processes after its interval CPU line. Those rows carry
    # `%CPU` and `MEM` figures the parser must not read as load, CPU, or disk.
    rows = (
        "PID    COMMAND          %CPU TIME     MEM    STATE\n"
        "1492   diskimagesiod    98.2 00:49.36 48M    running\n"
        "1676   apsd             61.0 03:10.02 12M    running\n"
    )
    with_rows = MAC_TELEMETRY.replace(
        "Interval CPU usage: 50.0% user, 46.0% sys, 4.0% idle\n",
        "Interval CPU usage: 50.0% user, 46.0% sys, 4.0% idle\n" + rows,
    )
    assert with_rows != MAC_TELEMETRY
    assert ct.parse_host_telemetry(with_rows) == ct.parse_host_telemetry(MAC_TELEMETRY)


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
    (
        "recovery_reboots",
        "bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py",
        '"rebooted Simulator %s after a failed cold runner spawn"',
        "rebooted Simulator 1A2B-3C after a failed cold runner spawn",
    ),
    (
        "device_replacements",
        "bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py",
        '"Simulator %s could not be recovered in place; shut it down and %s"',
        "Simulator 1A2B-3C could not be recovered in place; shut it down and created 4D5E",
    ),
    (
        "device_replacements",
        "bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py",
        '"Simulator %s vanished from CoreSimulator; %s"',
        "Simulator 1A2B-3C vanished from CoreSimulator; created 4D5E",
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
            return (
                "runner channel GET /elements failed (attempt 1/3), retrying: x\n"
                "simulator-services route=disable-then-reboot mode=stock reboot_seconds=90\n"
            )
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
    assert by_job["golden"]["simulator_services"] is None
    assert by_job["actuation"]["simulator_services"]["mode"] == "stock"
    assert by_job["golden"]["ci"]["run_id"] == "11"
    assert (out / "manifests" / "11-r1-20260101-000000" / "manifest.json").is_file()


@pytest.mark.parametrize(
    ("stderr", "log", "calls"),
    [
        # A `gh` that refuses escape sequences is asked again with the flag it names.
        ("pass --allow-escape-sequences to output it anyway", "colored log", 2),
        # Any other failure (expired logs) stays unknown, without a flag an older `gh` rejects.
        ("HTTP 410: Gone", None, 1),
    ],
)
def test_job_log_retries_only_on_the_escape_refusal(
    stderr: str, log: str | None, calls: int
) -> None:
    seen: list[list[str]] = []

    def gh(args: Sequence[str]) -> str:
        seen.append(list(args))
        if "--allow-escape-sequences" not in args:
            raise subprocess.CalledProcessError(1, "gh", stderr=stderr)
        return "colored log"

    assert ct._job_log(gh, "o/r", 7) == log
    assert len(seen) == calls


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


# --- BE-0517 adoption ------------------------------------------------------------------------


def test_simulator_services_reads_the_arm_and_both_counts() -> None:
    log = (
        "simulator-services disable_failed=3 of 169\n"
        "simulator-services route=disable-then-reboot mode=reduced reboot_seconds=120\n"
        "simulator-services start disabled=166 sim_procs=40\n"
        "simulator-services end disabled=12 sim_procs=95\n"
    )
    assert ct.simulator_services(log) == {"mode": "reduced", "start": 166, "end": 12}
    # A job that never reduced its device (every lane but run and actuation) belongs to no arm.
    assert ct.simulator_services("simulator-services end disabled=0 sim_procs=9\n") is None


def test_the_action_prints_the_lines_the_parser_reads() -> None:
    action = REPO / ".github/actions/reduce-simulator-services"
    assert "simulator-services route=disable-then-reboot mode=$mode" in (
        action / "apply.sh"
    ).read_text(encoding="utf-8")
    assert "simulator-services $label disabled=$disabled" in (action / "counts.sh").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("hits", "other_hits", "expected"),
    # The roadmap item's own worked numbers: 0 against 4 of 20 misses alpha, 0 against 5 makes it.
    [(0, 4, 0.0530), (0, 5, 0.0236), (0, 0, 1.0), (20, 0, 1.0)],
)
def test_fisher_one_sided(hits: int, other_hits: int, expected: float) -> None:
    assert ct.fisher_one_sided(hits, 20, other_hits, 20) == pytest.approx(expected, abs=1e-4)


def _job(
    mode: str | None,
    *,
    video: int = 0,
    retries: int = 0,
    reboots: int = 0,
    replacements: int = 0,
    event: str = "workflow_dispatch",
    started: str = "2026-12-01T01:00:00Z",
    end: int | None = 166,
    failed: str | None = None,
    red: bool = False,
) -> dict[str, Any]:
    signals = {
        "video_start_unconfirmed": video,
        "channel_retries": retries,
        "recovery_reboots": reboots,
        "device_replacements": replacements,
    }
    # `fetch` reads the arm and the signals from one log, so an unreadable log leaves both None.
    return {
        "api": {"job_name": "run (xcuitest)",
                "conclusion": "failure" if failed or red else "success", "started_at": started},
        "ci": {"run_id": "9"},
        "run_event": event,
        "run_branch": "main",
        "simulator_services": {"mode": mode, "start": 166, "end": end} if mode else None,
        "log_signals": signals if mode else None,
        "scenarios": [{"scenario": failed, "ok": False}] if failed else [],
    }  # fmt: skip


def _arms(reduced: list[dict[str, Any]], stock: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Pad each arm to the planned size with quiet jobs.
    pad = ct.ADOPTION_JOBS_PER_ARM
    return (
        reduced
        + [_job("reduced") for _ in range(pad - len(reduced))]
        + stock
        + [_job("stock") for _ in range(pad - len(stock))]
    )


def _adopt(records: list[dict[str, Any]], since: str = "") -> dict[str, Any]:
    return ct.adoption(records, since=since)


_STOCK_SIGNAL = [_job("stock", video=1)] * 5


def test_adoption_adopts_when_every_fixed_criterion_passes() -> None:
    result = _adopt(_arms([], _STOCK_SIGNAL))
    assert result["verdict"] == "adopt"
    assert result["video_start_p"] == pytest.approx(0.0236, abs=1e-4)
    assert "adoption: adopt" in ct.render_adoption(result)


def test_adoption_rejects_on_any_failing_criterion() -> None:
    # Four stock jobs leave the Fisher test short of alpha, as the roadmap item works out.
    assert _adopt(_arms([], [_job("stock", video=1)] * 4))["verdict"] == "reject"
    result = _adopt(_arms([_job("reduced", retries=3)], _STOCK_SIGNAL))
    assert result["criteria"]["channel_retries"] is False
    assert result["verdict"] == "reject"
    assert _adopt(_arms([_job("reduced", reboots=1)], _STOCK_SIGNAL))["verdict"] == "reject"
    # A replacement is a recovery as much as a reboot is.
    replaced = _adopt(_arms([_job("reduced", replacements=1)], _STOCK_SIGNAL))
    assert replaced["criteria"]["recoveries"] is False
    assert replaced["verdict"] == "reject"


def test_adoption_leaves_a_job_that_did_not_pass_to_a_person() -> None:
    result = _adopt(_arms([_job("reduced", failed="permission.yaml")], _STOCK_SIGNAL))
    assert result["verdict"] == "trace"
    assert result["criteria"]["scenarios"] is None
    rendered = ct.render_adoption(result)
    assert "run (xcuitest) (run 9): permission.yaml" in rendered
    assert "| no scenario traced to a turned-off service | open |" in rendered
    # A job that died before any scenario reported is traced too, not passed over.
    died = _adopt(_arms([_job("reduced", red=True)], _STOCK_SIGNAL))
    assert died["verdict"] == "trace"
    assert "run (xcuitest) (run 9): no scenario result" in ct.render_adoption(died)


def test_adoption_counts_only_fresh_dispatches() -> None:
    records = [
        *_arms([], _STOCK_SIGNAL),
        {**_job("reduced", video=1), "run_branch": "feature"},
        {
            **_job("reduced", video=1),
            "api": {"job_name": "golden (xcuitest)", "started_at": "2026-12-01T01:00:00Z"},
        },
        _job("reduced", video=1, event="pull_request"),
        _job("reduced", video=1, started="2026-11-26T01:00:00Z"),
    ]
    result = _adopt(records, since="2026-12-01T00:00:00Z")
    assert result["arms"]["reduced"]["jobs"] == ct.ADOPTION_JOBS_PER_ARM
    assert result["arms"]["reduced"]["video_start_unconfirmed_jobs"] == 0
    assert result["verdict"] == "adopt"


def test_adoption_lists_measured_jobs_whose_log_was_unreadable() -> None:
    unread = _job(None)
    other_lane = {**_job(None), "api": {"job_name": "golden (xcuitest)"}}
    result = _adopt([*_arms([], _STOCK_SIGNAL), unread, other_lane])
    assert result["unattributed_jobs"] == ["run (xcuitest) (run 9): log unreadable"]
    assert "with no arm in their log" in ct.render_adoption(result)


def test_adoption_lists_a_measured_job_that_stopped_before_printing_its_arm() -> None:
    # apply.sh exits under `set -e` before its route line when the reboot never completes.
    stopped = {**_job("reduced", red=True), "simulator_services": None}
    result = _adopt([*_arms([], _STOCK_SIGNAL), stopped])
    assert result["unattributed_jobs"] == ["run (xcuitest) (run 9): no route line"]


def test_adoption_is_incomplete_unless_each_arm_holds_exactly_the_plan() -> None:
    result = _adopt([_job("reduced"), _job("stock", video=1)])
    assert result["verdict"] == "incomplete"
    # An extra reduced job would add to that arm's totals for free.
    assert _adopt([*_arms([], _STOCK_SIGNAL), _job("reduced")])["verdict"] == "incomplete"
    assert _adopt([])["video_start_p"] is None


def test_adoption_counts_jobs_that_lost_the_change_midway() -> None:
    records = _arms([_job("reduced", end=0), _job("reduced", end=None)], _STOCK_SIGNAL)
    assert _adopt(records)["arms"]["reduced"]["lost_change_jobs"] == 1


def test_adoption_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in _arms([], [])), encoding="utf-8")
    since = ["--since", "2026-12-01T00:00:00Z"]
    assert ct.main(["adoption", str(path), *since]) == 0
    assert "# BE-0517 adoption: reject" in capsys.readouterr().out
    assert ct.main(["adoption", str(path), *since, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "reject"
    # Without a start time the pilot dispatches would count, so the flag is mandatory.
    with pytest.raises(SystemExit):
        ct.main(["adoption", str(path)])
    # Another timestamp shape would sort below every API timestamp and admit the whole history.
    assert ct.main(["adoption", str(path), "--since", "2026-12-01 00:00:00"]) == 2
    assert "--since must look like" in capsys.readouterr().err


def test_fetch_can_narrow_to_one_trigger(tmp_path: Path) -> None:
    record = ct.summarize(_runs(tmp_path, ok=False), "ios", env={"RUNNER_NAME": "r1"})
    gh = _FakeGh(record, _manifest(ok=False))
    ct.fetch(
        "o/r", "ios-e2e.yml", 5, tmp_path / "f", gh=gh, with_logs=False, event="workflow_dispatch"
    )
    listing = next(c for c in gh.calls if c[:2] == ["run", "list"])
    assert listing[-2:] == ["--event", "workflow_dispatch"]


def test_measure_dispatch_runs_only_the_measured_jobs() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ios-e2e.yml").read_text(encoding="utf-8"))
    on = workflow.get("on", workflow.get(True))
    assert on["workflow_dispatch"]["inputs"]["lanes"]["options"] == ["all", "measure"]
    # Alternating arms in the same hours must not cancel each other.
    assert "github.run_id" in workflow["concurrency"]["group"]
    measured = {"changes", "build", "run", "actuation", "e2e"}
    for name, job in workflow["jobs"].items():
        gated = "inputs.lanes != 'measure'" in str(job.get("if", ""))
        assert gated is (name not in measured), name

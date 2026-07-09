"""Tests for interval evidence: command builders and the start/stop lifecycle."""

from __future__ import annotations

import contextlib
import signal
import subprocess
from pathlib import Path

import pytest

from bajutsu import intervals, simctl


class FakeProc:
    def __init__(self) -> None:
        self.stopped_with: int | None = None

    def stop(self, sig: int) -> None:
        self.stopped_with = sig


def test_record_video_cmd() -> None:
    assert intervals.record_video_cmd("UDID", "/tmp/v.mp4") == [
        "xcrun",
        "simctl",
        "io",
        "UDID",
        "recordVideo",
        "--codec",
        "h264",
        "/tmp/v.mp4",
    ]


def test_interval_cmds_reject_unvalidated_udid() -> None:
    # These evidence-capture builders embed the udid in a simctl argv, so they validate it inline
    # (mirroring adb's `screenrecord_cmd`/`logcat_cmd` via `_checked_serial`) — a bad --udid can't
    # reach xcrun even if evidence capture is entered without the earlier Env-boundary check.
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        intervals.record_video_cmd("-rf; rm", "/tmp/v.mp4")
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        intervals.device_log_cmd("--set")
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        intervals.app_trace_cmd("a b", "com.x")


def test_device_log_cmd_with_and_without_predicate() -> None:
    base = intervals.device_log_cmd("UDID")
    assert base[:6] == ["xcrun", "simctl", "spawn", "UDID", "log", "stream"]
    assert "--predicate" not in base
    withp = intervals.device_log_cmd("UDID", 'subsystem == "com.x"')
    assert "--predicate" in withp and 'subsystem == "com.x"' in withp


def test_start_video_lifecycle() -> None:
    calls: list[tuple[list[str], Path | None]] = []
    proc = FakeProc()

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        calls.append((argv, stdout_path))
        return proc

    interval = intervals.start_video("UDID", Path("/tmp/v.mp4"), spawn=spawn)
    assert interval.kind == "video" and interval.provider == "simctl"
    argv, stdout_path = calls[0]
    assert argv == intervals.record_video_cmd("UDID", "/tmp/v.mp4")
    assert stdout_path is None  # recordVideo writes its own file
    assert interval.stop() == Path("/tmp/v.mp4")
    assert proc.stopped_with == signal.SIGINT  # SIGINT finalizes the mp4


def test_start_device_log_lifecycle() -> None:
    calls: list[tuple[list[str], Path | None]] = []
    proc = FakeProc()

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        calls.append((argv, stdout_path))
        return proc

    interval = intervals.start_device_log("UDID", Path("/tmp/d.log"), 'process == "X"', spawn=spawn)
    assert interval.kind == "deviceLog"
    argv, stdout_path = calls[0]
    assert argv == intervals.device_log_cmd("UDID", 'process == "X"')
    assert stdout_path == Path("/tmp/d.log")  # the stream is written to the file
    assert interval.stop() == Path("/tmp/d.log")
    assert proc.stopped_with == signal.SIGTERM


# --- adb (Android) interval providers ---


def test_start_screenrecord_records_device_side_then_pulls_on_stop(tmp_path: Path) -> None:
    from bajutsu import adb

    spawn_calls: list[tuple[list[str], Path | None]] = []
    run_calls: list[list[str]] = []
    proc = FakeProc()

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        spawn_calls.append((argv, stdout_path))
        return proc

    def run(argv: list[str]) -> str:
        run_calls.append(argv)
        return ""

    target = tmp_path / "scenario.mp4"
    interval = intervals.start_screenrecord("SER", target, spawn=spawn, run=run)
    assert interval.kind == "video" and interval.provider == "adb"

    argv, stdout_path = spawn_calls[0]
    assert argv == adb.screenrecord_cmd("SER")  # records to the device-side default path
    assert stdout_path is None  # screenrecord writes device-side, not to a host file

    assert interval.stop() == target
    assert proc.stopped_with == signal.SIGINT  # SIGINT finalizes the mp4
    # The recording is pulled off the device, then the device copy is removed — in that order.
    assert run_calls == [
        adb.pull_cmd("SER", adb.VIDEO_DEVICE_PATH, str(target)),
        adb.rm_cmd("SER", adb.VIDEO_DEVICE_PATH),
    ]


def test_start_screenrecord_cleanup_failure_does_not_fail_stop(tmp_path: Path) -> None:
    proc = FakeProc()
    ran: list[str] = []

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        return proc

    def run(argv: list[str]) -> str:
        if "pull" in argv:
            ran.append("pull")
            return ""
        if "rm" in argv:  # the device-side cleanup — allowed to fail without failing the run
            ran.append("rm")
            raise OSError("device gone")
        return ""

    target = tmp_path / "scenario.mp4"
    interval = intervals.start_screenrecord("SER", target, spawn=spawn, run=run)
    assert interval.stop() == target  # a failed cleanup is suppressed
    assert ran == ["pull", "rm"]  # the pull succeeded first; only the later cleanup failed


def test_start_screenrecord_pull_failure_surfaces(tmp_path: Path) -> None:
    # The pull is deliberately NOT suppressed: swallowing it would leave a video artifact path with
    # no file behind it. A failed pull must propagate out of stop() (the FileSink then drops it).
    proc = FakeProc()

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        return proc

    def run(argv: list[str]) -> str:
        if "pull" in argv:
            raise subprocess.CalledProcessError(1, argv)
        return ""

    interval = intervals.start_screenrecord("SER", tmp_path / "scenario.mp4", spawn=spawn, run=run)
    with pytest.raises(subprocess.CalledProcessError):
        interval.stop()


def test_start_logcat_streams_to_file(tmp_path: Path) -> None:
    from bajutsu import adb

    calls: list[tuple[list[str], Path | None]] = []
    proc = FakeProc()

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        calls.append((argv, stdout_path))
        return proc

    path = tmp_path / "device.log"
    interval = intervals.start_logcat("SER", path, spawn=spawn)
    assert interval.kind == "deviceLog" and interval.provider == "adb"
    argv, stdout_path = calls[0]
    assert argv == adb.logcat_cmd("SER")
    assert stdout_path == path  # the logcat stream is written to the file
    assert interval.stop() == path
    assert proc.stopped_with == signal.SIGTERM


def test_interval_kinds_registry() -> None:
    assert frozenset({"video", "deviceLog", "appTrace"}) == intervals.INTERVAL_KINDS


# --- appTrace: log-marker interval parsing ---

_NDJSON = "\n".join(
    [
        '{"eventType": "logEvent", "eventMessage": "reindex started",'
        ' "timestamp": "2026-06-05 01:01:11.681183+0900"}',
        '{"eventType": "logEvent", "eventMessage": "noise here",'
        ' "timestamp": "2026-06-05 01:01:11.900000+0900"}',
        "not json — should be skipped",
        '{"eventType": "logEvent", "eventMessage": "reindex finished",'
        ' "timestamp": "2026-06-05 01:01:12.881183+0900"}',
    ]
)


def test_parse_app_trace_pairs_markers() -> None:
    trace = intervals.parse_app_trace(_NDJSON)
    assert len(trace) == 1
    interval = trace[0]
    assert interval["name"] == "reindex"
    assert interval["durationMs"] == 1200.0  # 12.881 - 11.681 = 1.2s
    assert interval["begin"].startswith("2026-06-05T01:01:11")


def test_parse_app_trace_ignores_unpaired() -> None:
    text = (
        '{"eventType": "logEvent", "eventMessage": "load started",'
        ' "timestamp": "2026-06-05 01:01:11.000000+0900"}'
    )
    assert intervals.parse_app_trace(text) == []


def test_subprocess_proc_closes_file_on_popen_failure(tmp_path: Path, monkeypatch) -> None:
    """If Popen raises after the output file is opened, the file handle must be closed."""
    import subprocess as sp

    out = tmp_path / "out.log"
    opened_files: list = []
    _real_open = Path.open

    def tracking_open(self, *a, **kw):
        f = _real_open(self, *a, **kw)
        opened_files.append(f)
        return f

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(sp, "Popen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no")))

    with contextlib.suppress(OSError):
        intervals._SubprocessProc(["fake"], out)
    assert len(opened_files) == 1
    assert opened_files[0].closed, "file handle leaked — not closed after Popen failure"


def test_app_trace_cmd() -> None:
    cmd = intervals.app_trace_cmd("UDID", "com.x.app")
    assert cmd[:6] == ["xcrun", "simctl", "spawn", "UDID", "log", "stream"]
    assert "--predicate" in cmd and 'subsystem == "com.x.app"' in cmd
    assert "ndjson" in cmd


def test_start_app_trace_writes_parsed_json(tmp_path: Path) -> None:
    raw = tmp_path / "appTrace.raw"
    out = tmp_path / "appTrace.json"
    proc = FakeProc()

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        assert stdout_path == raw
        raw.write_text(_NDJSON, encoding="utf-8")  # the "stream" writes raw ndjson
        return proc

    interval = intervals.start_app_trace("UDID", raw, out, "com.x.app", spawn=spawn)
    assert interval.kind == "appTrace"
    assert interval.stop() == out  # transform turns raw -> parsed json
    import json as _json

    parsed = _json.loads(out.read_text())
    assert parsed[0]["name"] == "reindex" and parsed[0]["durationMs"] == 1200.0

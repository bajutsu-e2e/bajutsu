"""Tests for interval evidence: command builders and the start/stop lifecycle."""

from __future__ import annotations

import signal
from pathlib import Path

from bajutsu import intervals


class FakeProc:
    def __init__(self) -> None:
        self.stopped_with: int | None = None

    def stop(self, sig: int) -> None:
        self.stopped_with = sig


def test_record_video_cmd() -> None:
    assert intervals.record_video_cmd("UDID", "/tmp/v.mp4") == [
        "xcrun", "simctl", "io", "UDID", "recordVideo", "--codec", "h264", "/tmp/v.mp4",
    ]


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


def test_interval_kinds_registry() -> None:
    assert intervals.INTERVAL_KINDS == frozenset({"video", "deviceLog", "appTrace"})


# --- appTrace: log-marker interval parsing ---

_NDJSON = "\n".join([
    '{"eventType": "logEvent", "eventMessage": "reindex started",'
    ' "timestamp": "2026-06-05 01:01:11.681183+0900"}',
    '{"eventType": "logEvent", "eventMessage": "noise here",'
    ' "timestamp": "2026-06-05 01:01:11.900000+0900"}',
    "not json — should be skipped",
    '{"eventType": "logEvent", "eventMessage": "reindex finished",'
    ' "timestamp": "2026-06-05 01:01:12.881183+0900"}',
])


def test_parse_app_trace_pairs_markers() -> None:
    trace = intervals.parse_app_trace(_NDJSON)
    assert len(trace) == 1
    interval = trace[0]
    assert interval["name"] == "reindex"
    assert interval["durationMs"] == 1200.0  # 12.881 - 11.681 = 1.2s
    assert interval["begin"].startswith("2026-06-05T01:01:11")


def test_parse_app_trace_ignores_unpaired() -> None:
    text = '{"eventType": "logEvent", "eventMessage": "load started",' \
        ' "timestamp": "2026-06-05 01:01:11.000000+0900"}'
    assert intervals.parse_app_trace(text) == []


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

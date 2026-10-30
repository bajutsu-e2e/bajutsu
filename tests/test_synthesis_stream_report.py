"""Tests for BE-0462 Unit 1's measurement scripts — the log-stream recorder and its report.

The report's numbers decide the host monitor's bound and whether a fence can close a window, so the
pieces that turn a recording into those numbers are pinned here against hand-built streams: marker
parsing, window nesting, drop attribution (including a drop no window covers), arrival-order
inversions, and the lag percentiles.
"""

from __future__ import annotations

import io
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import synthesis_stream_record
from scripts.synthesis_stream_record import record, stream_cmd, to_record
from scripts.synthesis_stream_report import (
    analyze,
    find_recordings,
    main,
    parse_marker,
    parse_ts,
    percentiles,
    render,
)

T0 = parse_ts("2026-10-29 12:00:00.000000+0000")


def _stamp(offset: float) -> str:
    whole = int(offset)
    micros = round((offset - whole) * 1_000_000)
    return f"2026-10-29 12:00:{whole:02d}.{micros:06d}+0000"


def _runner(offset: float, text: str, lag: float = 0.01) -> dict[str, object]:
    return {
        "host_ts": T0 + offset + lag,
        "ts": _stamp(offset),
        "process": "BajutsuRunnerUITests-Runner",
        "subsystem": "bajutsu.runner",
        "category": "synthesis",
        "message": f"bajutsu.synthesis {text}",
    }


def _bb(
    offset: float, text: str, lag: float = 0.02, category: str = "EventDelivery"
) -> dict[str, object]:
    return {
        "host_ts": T0 + offset + lag,
        "ts": _stamp(offset),
        "process": "backboardd",
        "subsystem": "com.apple.BackBoard",
        "category": category,
        "message": text,
    }


def _write(path: Path, records: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_parse_marker_reads_kind_and_key() -> None:
    assert parse_marker("bajutsu.synthesis begin d548a947/12") == ("begin", "d548a947/12")
    assert parse_marker("bajutsu.synthesis serving d548a947") == ("serving", "d548a947")
    assert parse_marker("touch down at (1, 2)") is None


def test_drop_in_nested_monitor_window_is_attributed_to_the_inner_window(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "a" / "synthesis-stream.ndjson",
        [
            _runner(1.0, "begin c0/1"),
            _runner(1.1, "begin c0/2"),
            _bb(1.15, "*** unknown digitizer", category="HID"),
            _runner(1.2, "end c0/2"),
            _runner(1.2, "fence c0/2"),
            _runner(1.5, "end c0/1"),
            _runner(1.5, "fence c0/1"),
        ],
    )
    report = analyze([rec])
    assert report["windows"] == 2
    assert [w["key"] for w in report["dropped_windows"]] == ["c0/2"]
    assert report["drop_categories"] == {"HID": 1}
    assert report["unattributed_drops"] == []


def test_drop_outside_every_window_is_reported_unattributed(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [
            _runner(1.0, "begin c0/1"),
            _runner(1.2, "end c0/1"),
            _bb(1.25, "didn't see a previous touch down for pathIndex:2; skipping event"),
            _runner(1.2, "fence c0/1"),
        ],
    )
    report = analyze([rec])
    assert report["dropped_windows"] == []
    assert len(report["unattributed_drops"]) == 1


def test_inversion_counts_backboardd_lines_that_arrive_behind_a_later_runner_line(
    tmp_path: Path,
) -> None:
    # File order is arrival order: the backboardd line stamped at 1.1 arrives after the end marker
    # stamped at 1.3 — exactly what would let a fence close a window too early.
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [
            _runner(1.0, "begin c0/1"),
            _runner(1.3, "end c0/1"),
            _bb(1.1, "touch delivered", lag=0.5),
            _bb(1.4, "later line"),
        ],
    )
    inv = analyze([rec])["inversions"]
    assert inv["count"] == 1
    assert abs(inv["worst"][0]["ahead"] - 0.2) < 1e-6


def test_lag_is_measured_per_marker_kind_and_for_backboardd(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [
            _runner(0.5, "serving c0", lag=0.3),
            _runner(1.0, "begin c0/1", lag=0.05),
            _runner(1.2, "end c0/1", lag=0.10),
            _bb(1.1, "x", lag=0.2),
        ],
    )
    lag = analyze([rec])["lag"]
    assert set(lag) == {"marker:serving", "marker:begin", "marker:end", "backboardd"}
    assert abs(lag["marker:end"]["max"] - 0.10) < 1e-3
    assert abs(lag["backboardd"]["p50"] - 0.2) < 1e-3


def test_window_left_open_takes_no_drops(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [_runner(1.0, "begin c0/1"), _bb(2.0, "unknown digitizer")],
    )
    report = analyze([rec])
    assert report["windows_never_closed"] == 1
    # A window with no end has no known span: the drop is reported unattributed, not absorbed.
    assert report["dropped_windows"] == []
    assert len(report["unattributed_drops"]) == 1


def test_end_arriving_before_its_begin_still_closes_the_window(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [
            _runner(1.2, "end c0/1"),
            _runner(1.0, "begin c0/1"),
            _bb(1.1, "unknown digitizer"),
        ],
    )
    report = analyze([rec])
    assert report["windows_never_closed"] == 0
    assert [w["key"] for w in report["dropped_windows"]] == ["c0/1"]


def test_respawned_runner_keys_do_not_collide(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [
            _runner(1.0, "begin a/1"),
            _runner(1.1, "end a/1"),
            _runner(2.0, "begin b/1"),
            _bb(2.05, "unknown digitizer"),
            _runner(2.1, "end b/1"),
        ],
    )
    assert [w["key"] for w in analyze([rec])["dropped_windows"]] == ["b/1"]


def test_a_truncated_last_line_is_tolerated_but_other_bad_lines_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "synthesis-stream.ndjson"
    good = json.dumps(_runner(1.0, "begin c0/1"))
    path.write_text(f"{good}\nnot json\n{good}\n{{truncated", encoding="utf-8")
    report = analyze([path])
    assert report["windows"] == 1
    assert report["health"][0]["bad_lines"] == 1


def test_meta_records_surface_recorder_health(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [
            {"host_ts": T0, "meta": "start", "session": 1},
            _bb(1.0, "x"),
            {"host_ts": T0 + 2, "meta": "end", "session": 1, "rc": 1, "written": 1, "skipped": 3},
            {"host_ts": T0 + 4, "meta": "start", "session": 2},
            {"host_ts": T0 + 5, "meta": "end", "session": 2, "rc": 1, "written": 0, "skipped": 0},
            {"host_ts": T0 + 6, "meta": "gave-up", "sessions": 2},
        ],
    )
    report = analyze([rec])
    assert report["health"][0]["sessions"] == 2
    assert report["health"][0]["events"] == 1
    warnings = "\n".join(report["warnings"])
    assert "no runner markers" in warnings
    assert "skipped 3 stream line(s)" in warnings
    assert "ended 2 time(s) mid-job" in warnings
    assert "killed outright" not in warnings
    assert "gave up" in warnings


def test_an_empty_recording_warns_instead_of_reading_as_a_quiet_run(tmp_path: Path) -> None:
    rec = _write(tmp_path / "synthesis-stream.ndjson", [])
    warnings = "\n".join(analyze([rec])["warnings"])
    assert "no recorder session started" in warnings
    assert "no events recorded" in warnings
    assert "WARNING" in render(analyze([rec]))


def test_percentiles_use_nearest_rank() -> None:
    stats = percentiles([float(i) for i in range(1, 101)])
    assert (stats["p50"], stats["p95"], stats["p99"], stats["max"]) == (50.0, 95.0, 99.0, 100.0)
    # ceil(0.95 * 30) = 29: rounding instead would pick the 28th and understate the tail.
    assert percentiles([float(i) for i in range(1, 31)])["p95"] == 29.0
    assert percentiles([]) == {"n": 0}


def test_find_recordings_walks_artifact_directories(tmp_path: Path) -> None:
    a = _write(tmp_path / "job1" / "diagnostics" / "synthesis-stream.ndjson", [])
    b = _write(tmp_path / "job2" / "diagnostics" / "synthesis-stream.ndjson", [])
    assert find_recordings([tmp_path]) == [a, b]


def test_main_renders_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(
        tmp_path / "synthesis-stream.ndjson",
        [_runner(1.0, "begin c0/1"), _bb(1.05, "unknown digitizer"), _runner(1.1, "end c0/1")],
    )
    assert main([str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "dropped windows: 1" in text
    assert "--- c0/1" in text
    assert main([str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["recordings"] == 1
    assert main([str(tmp_path / "missing")]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_render_tolerates_an_empty_report(tmp_path: Path) -> None:
    rec = _write(tmp_path / "synthesis-stream.ndjson", [])
    assert "dropped windows: 0" in render(analyze([rec]))


def test_recorder_stamps_each_event_and_skips_the_preamble() -> None:
    event = {
        "timestamp": "2026-10-29 12:01:14.690123+0000",
        "processImagePath": "/usr/libexec/backboardd",
        "subsystem": "com.apple.BackBoard",
        "category": "HID",
        "messageType": "Default",
        "eventMessage": "unknown digitizer",
    }
    out = io.StringIO()
    counts = record(
        [
            (10.0, b"Filtering the log data using ...\r"),
            (11.0, json.dumps(event).encode()),
            (12.0, b"{not an event}"),
        ],
        out,
    )
    # The preamble is neither written nor skipped; only the line that should have been an event is.
    assert counts == (1, 1)
    rec = json.loads(out.getvalue())
    assert rec["host_ts"] == 11.0
    assert rec["process"] == "backboardd"
    assert rec["message"] == "unknown digitizer"
    assert to_record("[1, 2]", 0.0) is None


def test_recorder_streams_the_runner_and_backboardd_as_ndjson() -> None:
    cmd = stream_cmd("UDID-1")
    assert cmd[:6] == ["xcrun", "simctl", "spawn", "UDID-1", "log", "stream"]
    assert "ndjson" in cmd
    predicate = cmd[cmd.index("--predicate") + 1]
    assert '"bajutsu.runner"' in predicate
    assert '"backboardd"' in predicate


def _fake_stream(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    monkeypatch.setattr(
        synthesis_stream_record, "stream_cmd", lambda udid: ["printf", "%s", payload]
    )


_EVENT_LINE = json.dumps(
    {"timestamp": "2026-10-29 12:01:14.690123+0000", "eventMessage": "bajutsu.synthesis begin c0/1"}
)


def test_stream_once_reads_the_child_through_a_pty(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stream(monkeypatch, f"Filtering the log data\n{_EVENT_LINE}\n")
    out = io.StringIO()
    assert synthesis_stream_record.stream_once("UDID-1", out) == (0, 1, 0)
    assert json.loads(out.getvalue())["message"] == "bajutsu.synthesis begin c0/1"


def test_main_restarts_the_stream_and_gives_up_only_after_no_session_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first session writes an event, the second writes none; with no grace period left, the
    # empty one ends the recording, while the productive one did not.
    payloads = iter([f"{_EVENT_LINE}\n", ""])
    monkeypatch.setattr(
        synthesis_stream_record, "stream_cmd", lambda udid: ["printf", "%s", next(payloads)]
    )
    monkeypatch.setattr(synthesis_stream_record, "GIVE_UP_AFTER", 0.0)
    monkeypatch.setattr(synthesis_stream_record, "RESTART_PAUSE", 0.0)
    out = tmp_path / "diag" / "synthesis-stream.ndjson"
    assert synthesis_stream_record.main(["--udid", "UDID-1", "--out", str(out)]) == 1
    assert out.stat().st_mode & 0o777 == 0o600
    metas = [r["meta"] for r in map(json.loads, out.read_text().splitlines()) if "meta" in r]
    assert metas == ["start", "end", "start", "end", "gave-up"]
    health = analyze([out])["health"][0]
    assert (health["sessions"], health["events"], health["stream_ends"]) == (2, 1, 2)


def test_a_session_with_no_terminator_reads_as_a_killed_recorder(tmp_path: Path) -> None:
    rec = _write(
        tmp_path / "synthesis-stream.ndjson",
        [
            {"host_ts": T0, "meta": "start", "session": 1},
            _runner(1.0, "begin c0/1"),
            {"host_ts": T0 + 2, "meta": "stopped"},
            {"host_ts": T0 + 3, "meta": "start", "session": 2},
            _runner(4.0, "begin c0/2"),
        ],
    )
    report = analyze([rec])
    # The first session was stopped by the action; only the second, cut off with no record, warns.
    assert any("1 session(s) have no `end` or `stopped` record" in w for w in report["warnings"])


def test_sigterm_closes_the_running_session_with_a_stopped_record(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "xcrun").write_text("#!/bin/bash\nexec sleep 30\n")
    (bin_dir / "xcrun").chmod(0o755)
    out = tmp_path / "synthesis-stream.ndjson"
    script = Path(synthesis_stream_record.__file__)
    proc = subprocess.Popen(
        [sys.executable, str(script), "--udid", "UDID-1", "--out", str(out)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        stderr=subprocess.DEVNULL,
    )
    try:
        # A bounded wait on the condition the test needs, not a fixed pause.
        deadline = time.monotonic() + 10
        while not (out.exists() and out.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()
    metas = [r["meta"] for r in map(json.loads, out.read_text().splitlines()) if "meta" in r]
    assert metas == ["start", "stopped"]
    assert analyze([out])["warnings"] == [
        f"{out}: no events recorded",
    ]

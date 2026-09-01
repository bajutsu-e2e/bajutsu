"""Tests for interval evidence: command builders and the start/stop lifecycle."""

from __future__ import annotations

import contextlib
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest
from conftest import json_str

from bajutsu.common import stall_diagnostics
from bajutsu.common.backend_cli import simctl
from bajutsu.evidence import intervals


class FakeProc:
    def __init__(self) -> None:
        self.stopped_with: int | None = None
        self.stopped_timeout: float | None = None

    def stop(self, sig: int, timeout: float) -> None:
        self.stopped_with = sig
        self.stopped_timeout = timeout


class FakeDevice:
    """An `adb` runner answering the two distinct probes `start_screenrecord` makes.

    "Is the recording process there?" (`pgrep`) and "is it producing bytes?" (the file's size) are
    different questions with different answers, so a fake serving one canned string for both would
    let a pid be read as a byte count. Each sequence serves its entries in order and then holds its
    last, the same shape the driver-side act fakes use.
    """

    def __init__(self, *, pids: list[str] | None = None, sizes: list[str] | None = None) -> None:
        self._pids = list(pids or [""])
        self._sizes = list(sizes or ["0"])
        self.calls: list[list[str]] = []

    @staticmethod
    def _next(seq: list[str]) -> str:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        tail = argv[-1]
        if "pgrep" in tail:
            return self._next(self._pids)
        if "stat -c" in tail:
            return self._next(self._sizes)
        return ""

    def size_probes(self) -> list[list[str]]:
        return [argv for argv in self.calls if "stat -c" in argv[-1]]


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
    # (mirroring adb's `screenrecord_cmd`/`logcat_cmd` via `checked_serial`) — a bad --udid can't
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
    # Video gets the generous finalize window, not the short log-stream grace: a premature kill would
    # truncate the mp4 (no moov atom) and wedge the simulator's recording session.
    assert proc.stopped_timeout == intervals._VIDEO_FINALIZE_TIMEOUT


def test_start_video_confirm_started_false_by_default_leaves_true_start_none() -> None:
    interval = intervals.start_video("UDID", Path("/tmp/v.mp4"), spawn=lambda argv, out: FakeProc())
    assert interval.true_start is None  # no poll attempted; every existing caller is unaffected


def test_start_video_confirm_started_sets_true_start_once_file_grows(tmp_path: Path) -> None:
    # recordVideo writes progressively to its output path; spawn writing the first byte proves the
    # confirmation is a real condition wait on that write, not a fixed sleep — it returns on the
    # very first poll, with no monkeypatched clock needed.
    path = tmp_path / "v.mp4"

    def spawn(argv: list[str], out: Path | None) -> FakeProc:
        path.write_bytes(b"clip")  # simulates recordVideo's first written byte
        return FakeProc()

    interval = intervals.start_video("UDID", path, spawn=spawn, confirm_started=True)
    assert isinstance(interval.true_start, float)


def test_video_start_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # A loaded CI host can extend the recording-start confirmation ceiling without a code change
    # (the ios-e2e workflow raises it), the treatment its three sibling xcuitest timeouts already
    # have; a blank or malformed value falls back to the compiled default (BE-0348).
    monkeypatch.delenv(intervals._VIDEO_START_TIMEOUT_ENV, raising=False)
    assert intervals._video_start_timeout() == intervals._VIDEO_START_TIMEOUT
    monkeypatch.setenv(intervals._VIDEO_START_TIMEOUT_ENV, "20")
    assert intervals._video_start_timeout() == 20.0
    monkeypatch.setenv(intervals._VIDEO_START_TIMEOUT_ENV, "not-a-number")
    assert intervals._video_start_timeout() == intervals._VIDEO_START_TIMEOUT


def test_video_start_timeout_rejects_non_finite_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    # `float()` parses "inf"/"-inf"/"nan" successfully, so the plain `except ValueError` fallback
    # does not catch them. Left unguarded, "inf" would make `deadline = time.monotonic() + timeout`
    # unreachable in `_await_video_file_growing`/`_await_screenrecord_started` — an unbounded wait,
    # which prime directive 2 (determinism first) forbids outright — and "nan" would silently produce
    # a 0-second timeout via `max(0.0, nan) == 0.0` (every comparison against nan is False) rather
    # than falling back to the compiled default like any other malformed value.
    for raw in ("inf", "-inf", "Infinity", "nan"):
        monkeypatch.setenv(intervals._VIDEO_START_TIMEOUT_ENV, raw)
        assert intervals._video_start_timeout() == intervals._VIDEO_START_TIMEOUT, raw


def test_confirming_starters_resolve_the_timeout_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The override is useless if a starter binds the ceiling as a parameter default: a default is
    # evaluated at import time, long before a test (or a CI lane) sets the variable. Both production
    # call sites must therefore resolve it per call — proven by setting the variable *after* import
    # and watching the deadline each starter actually polls to (BE-0348).
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setenv(intervals._VIDEO_START_TIMEOUT_ENV, "0.01")
    seen: list[float] = []

    def record(timeout: float) -> None:
        seen.append(timeout)

    monkeypatch.setattr(
        intervals,
        "_await_video_file_growing",
        lambda path, baseline, timeout: record(timeout),
    )
    intervals.start_video(
        "UDID", tmp_path / "v.mp4", spawn=lambda a, o: FakeProc(), confirm_started=True
    )

    monkeypatch.setattr(
        intervals,
        "_await_screenrecord_started",
        lambda serial, run, baseline, timeout: record(timeout),
    )
    intervals.start_screenrecord(
        "SER",
        tmp_path / "a.mp4",
        spawn=lambda a, o: FakeProc(),
        run=lambda argv: "",
        confirm_started=True,
    )
    assert seen == [0.01, 0.01]


def test_start_video_separates_an_unconfirmed_start_from_an_unattempted_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0354: `true_start` is None both for a capture nobody confirmed and for one whose confirmation
    # timed out, and only the second says the device's capture pipeline is not producing — the signal
    # the crash retry picks its recovery rung from. `start_confirmed` is what tells them apart.
    unattempted = intervals.start_video(
        "UDID", tmp_path / "a.mp4", spawn=lambda argv, out: FakeProc()
    )
    assert unattempted.start_confirmed is None

    # The confirmation's own polling is covered below; here it is stubbed to its give-up answer so the
    # case costs no wall time.
    monkeypatch.setattr(intervals, "_await_video_file_growing", lambda *_a, **_k: None)
    stalled = intervals.start_video(
        "UDID", tmp_path / "b.mp4", spawn=lambda argv, out: FakeProc(), confirm_started=True
    )
    assert stalled.start_confirmed is False

    monkeypatch.setattr(intervals, "_await_video_file_growing", lambda *_a, **_k: 12.0)
    confirmed = intervals.start_video(
        "UDID", tmp_path / "c.mp4", spawn=lambda argv, out: FakeProc(), confirm_started=True
    )
    assert confirmed.start_confirmed is True


def test_a_recording_that_never_produces_bytes_captures_the_stall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0361 unit 2's second trigger. A dead video pipeline is the same degradation the runner
    # channel dies of, so the capture fires here too — but only on the stall, never on a healthy
    # start, and never on a capture nobody asked to confirm.
    captured: list[tuple[str, str | None]] = []
    # `simulator_probes` hands back its udid, so the recorded pair names the device the probes would
    # have screenshotted as well as the trigger that fired.
    monkeypatch.setattr(stall_diagnostics, "simulator_probes", lambda udid=None: udid)
    monkeypatch.setattr(
        stall_diagnostics, "capture", lambda reason, probes: captured.append((reason, probes))
    )

    monkeypatch.setattr(intervals, "_await_video_file_growing", lambda *_a, **_k: 12.0)
    intervals.start_video(
        "UDID", tmp_path / "ok.mp4", spawn=lambda argv, out: FakeProc(), confirm_started=True
    )
    intervals.start_video("UDID", tmp_path / "unattempted.mp4", spawn=lambda argv, out: FakeProc())
    assert captured == []

    monkeypatch.setattr(intervals, "_await_video_file_growing", lambda *_a, **_k: None)
    intervals.start_video(
        "UDID", tmp_path / "stalled.mp4", spawn=lambda argv, out: FakeProc(), confirm_started=True
    )
    assert captured == [("video-no-bytes", "UDID")]


def test_adopt_carries_the_start_confirmation_onto_the_relocated_capture(tmp_path: Path) -> None:
    # Android hands its pre-launch recording over for the sink to adopt; whether that recording ever
    # confirmed it began does not change because its file is later moved.
    running = intervals.Interval(kind="video", path=tmp_path / "tmp.mp4", start_confirmed=False)
    assert intervals.adopt(running, tmp_path / "final.mp4").start_confirmed is False


def test_await_video_file_growing_ignores_bytes_left_by_a_stale_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A crash-retry reuses the same scenario id and thus the same target path (BE-0049): a finalized
    # earlier attempt's mp4 can already sit at `path` when this attempt spawns. Without a pre-spawn
    # baseline, the very first poll would misread those leftover bytes as this attempt's own first
    # frame — confirming a start that never happened. The size must grow *past* what was already
    # there (the baseline `start_video` captures before spawning), not just be non-zero.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    path = tmp_path / "v.mp4"
    path.write_bytes(b"leftover from a finalized earlier attempt")
    baseline = intervals._file_size(path)
    # This attempt's recordVideo never actually writes (e.g. the target already exists) — the file
    # stays exactly at the baseline size throughout the poll.
    result = intervals._await_video_file_growing(path, baseline, timeout=0.01, poll=0.001)
    assert result is None


def test_await_video_file_growing_confirms_growth_past_a_nonzero_baseline(tmp_path: Path) -> None:
    path = tmp_path / "v.mp4"
    path.write_bytes(b"old")
    baseline = intervals._file_size(path)
    path.write_bytes(b"old + new frame")
    result = intervals._await_video_file_growing(path, baseline)
    assert isinstance(result, float)


def test_file_size_missing_file_stays_silent(caplog: pytest.LogCaptureFixture) -> None:
    # The common case (recordVideo hasn't written anything yet) must not warn even when disclose
    # is requested — only a genuine "can't tell" failure should.
    with caplog.at_level("WARNING"):
        result = intervals._file_size(Path("/nonexistent/never-written.mp4"), disclose=True)
    assert result == 0
    assert not caplog.records


def test_file_size_disclose_warns_on_a_non_missing_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A 0 from a failure that is not "the file doesn't exist yet" (a permission error, EIO, an
    # unreadable run dir) reads exactly like "no leftover bytes" and would silently defeat the
    # stale-retry guard the pre-spawn baseline exists for — so, unlike the missing-file case, this
    # must warn.
    def raising_stat(self: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "stat", raising_stat)
    with caplog.at_level("WARNING"):
        result = intervals._file_size(Path("/some/path.mp4"), disclose=True)
    assert result == 0
    assert any("could not size" in r.message for r in caplog.records)


def test_file_size_without_disclose_stays_silent_on_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The per-poll caller (inside _await_video_file_growing) must never warn on every failed poll —
    # only the one-time baseline call opts into disclosure.
    def raising_stat(self: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "stat", raising_stat)
    with caplog.at_level("WARNING"):
        result = intervals._file_size(Path("/some/path.mp4"))
    assert result == 0
    assert not caplog.records


def test_await_video_file_growing_warns_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A file that never grows (recordVideo never wrote a frame) must not hang the caller — the poll
    # gives up at the deadline and leaves true_start unconfirmed, with a warning so a scenario whose
    # video never started is diagnosable rather than silently mistimed.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with caplog.at_level("WARNING"):
        result = intervals._await_video_file_growing(
            tmp_path / "never-written.mp4", 0, timeout=0.01, poll=0.001
        )
    assert result is None
    assert any("produced no new bytes" in r.message for r in caplog.records)


def test_adopt_finalizes_then_relocates_to_target(tmp_path: Path) -> None:
    # A device backend starts recording before launch into a temp path; the sink adopts the running
    # interval and, on stop, finalizes it (real signal/timeout) and moves the file to the artifact
    # path. Prove the wrapped stop runs and the finalized file lands at the target.
    proc = FakeProc()
    temp = tmp_path / "_tmp" / "prestart-UDID.mp4"
    temp.parent.mkdir()
    temp.write_bytes(b"clip")
    running = intervals.start_video("UDID", temp, spawn=lambda argv, out: proc)

    target = tmp_path / "scenario" / "scenario.mp4"
    adopted = intervals.adopt(running, target)
    assert adopted.kind == "video" and adopted.path == target

    assert adopted.stop() == target
    assert proc.stopped_with == signal.SIGINT  # the wrapped interval's real finalize still runs
    assert proc.stopped_timeout == intervals._VIDEO_FINALIZE_TIMEOUT
    assert target.read_bytes() == b"clip" and not temp.exists()  # moved, not copied


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
    assert (
        proc.stopped_timeout == intervals._STOP_TIMEOUT
    )  # a log stream ends at once — short grace


# --- adb (Android) interval providers ---


def test_start_screenrecord_records_device_side_then_pulls_on_stop(tmp_path: Path) -> None:
    from bajutsu.common.backend_cli import adb

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
    assert proc.stopped_timeout == intervals._VIDEO_FINALIZE_TIMEOUT  # same generous flush window
    # The device-side screenrecord's exit is awaited (so the moov atom is written), then the mp4 is
    # pulled, then the device copy is removed — in that order. The fake `run` reports no pid, so the
    # wait clears on its first poll.
    assert run_calls == [
        adb.screenrecord_pids_cmd("SER"),
        adb.pull_cmd("SER", adb.VIDEO_DEVICE_PATH, str(target)),
        adb.rm_cmd("SER", adb.VIDEO_DEVICE_PATH),
    ]


def test_start_screenrecord_forwards_bound_and_size_options(tmp_path: Path) -> None:
    from bajutsu.common.backend_cli import adb

    spawn_calls: list[list[str]] = []

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        spawn_calls.append(argv)
        return FakeProc()

    def run(argv: list[str]) -> str:
        return ""

    intervals.start_screenrecord(
        "SER",
        tmp_path / "scenario.mp4",
        spawn=spawn,
        run=run,
        time_limit=900,
        size="540x1200",
        bit_rate=2_000_000,
    )
    assert spawn_calls[0] == adb.screenrecord_cmd(
        "SER", time_limit=900, size="540x1200", bit_rate=2_000_000
    )


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


def test_screenrecord_pids_cmd_tolerates_no_match() -> None:
    from bajutsu.common.backend_cli import adb

    # `|| true` keeps a no-match pgrep at exit 0 so the RunFn (check=True) doesn't raise; the poll
    # reads the device-side process's presence from stdout, not the exit code.
    cmd = adb.screenrecord_pids_cmd("SER")
    assert cmd == ["adb", "-s", "SER", "shell", "pgrep -x screenrecord || true"]


def test_start_screenrecord_waits_for_device_side_exit_before_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The device-side screenrecord finalizes the moov atom after the local adb client returns; the
    # transform must poll until it exits before pulling, else the pull races into a truncated mp4.
    monkeypatch.setattr(time, "sleep", lambda _s: None)  # no real waiting in the test
    proc = FakeProc()
    order: list[str] = []
    pid_replies = iter(["1234", "1234", ""])  # still recording, still recording, then gone

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        return proc

    def run(argv: list[str]) -> str:
        if "pgrep" in " ".join(argv):
            order.append("poll")
            return next(pid_replies)
        order.append("pull" if "pull" in argv else "rm" if "rm" in argv else "other")
        return ""

    interval = intervals.start_screenrecord("SER", tmp_path / "scenario.mp4", spawn=spawn, run=run)
    assert interval.stop() == tmp_path / "scenario.mp4"
    # Polled until the pid list came back empty, and only then pulled (never before).
    assert order == ["poll", "poll", "poll", "pull", "rm"]


def test_await_screenrecord_stopped_warns_on_probe_error(caplog: pytest.LogCaptureFixture) -> None:
    # A probe that errors must not hang the pull, but the fallback can't be silent — it may pull a
    # still-finalizing (truncated) mp4, the failure the wait exists to prevent.
    def run(argv: list[str]) -> str:
        raise OSError("adb gone")

    with caplog.at_level("WARNING"):
        intervals._await_screenrecord_stopped("SER", run)
    assert any("could not probe" in r.message for r in caplog.records)


def test_await_screenrecord_stopped_warns_on_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # If screenrecord never exits, the wait gives up at the deadline and pulls anyway — with a warning
    # so a truncated recording is diagnosable rather than silent.
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def run(argv: list[str]) -> str:
        return "1234"  # device-side screenrecord always reports as still running

    with caplog.at_level("WARNING"):
        intervals._await_screenrecord_stopped("SER", run, timeout=0.01, poll=0.001)
    assert any("still running" in r.message for r in caplog.records)


def test_start_screenrecord_confirm_started_false_by_default_leaves_true_start_none(
    tmp_path: Path,
) -> None:
    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        return FakeProc()

    def run(argv: list[str]) -> str:
        return ""

    interval = intervals.start_screenrecord("SER", tmp_path / "scenario.mp4", spawn=spawn, run=run)
    assert interval.true_start is None  # no poll attempted; every existing caller is unaffected


def test_start_screenrecord_confirm_started_sets_true_start_on_first_poll(tmp_path: Path) -> None:
    # A new pid on the very first poll (none was present at the pre-spawn baseline) proves the
    # confirmation is a real condition wait on the device-side process, not a fixed sleep — no
    # monkeypatched clock needed.
    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        return FakeProc()

    device = FakeDevice(pids=["", "1234"], sizes=["0", "512"])

    interval = intervals.start_screenrecord(
        "SER", tmp_path / "scenario.mp4", spawn=spawn, run=device, confirm_started=True
    )
    assert isinstance(interval.true_start, float)


def test_start_screenrecord_confirm_started_ignores_a_leaked_pid_from_a_stale_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A leaked screenrecord from a crash-retry (BE-0049), or any other process already running on
    # the same device, must not confirm a start that never happened — only a *new* pid, absent from
    # the pre-spawn baseline, counts.
    #
    # `_await_screenrecord_started`'s 5s timeout is a default argument bound at import, so patching
    # `_VIDEO_START_TIMEOUT` cannot shorten it — drive its deadline instead: one real poll (which
    # must see only the leaked pid), then a jump past the deadline.
    monotonic_calls: list[int] = []

    def monotonic() -> float:
        monotonic_calls.append(1)
        return 0.0 if len(monotonic_calls) <= 2 else 1e6

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def spawn(argv: list[str], stdout_path: Path | None) -> FakeProc:
        return FakeProc()

    def run(argv: list[str]) -> str:
        return "1234"  # the same leaked pid, before spawn and on every later poll

    interval = intervals.start_screenrecord(
        "SER", tmp_path / "scenario.mp4", spawn=spawn, run=run, confirm_started=True
    )
    assert interval.true_start is None


def test_start_screenrecord_warns_and_captures_when_the_recording_never_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The process existing is all `_await_screenrecord_started` can see, and a wedged renderer
    # leaves it alive with an empty file — the difference between a slow run and a stalled one
    # (BE-0367). Growth never confirming must warn and fire the stall probe.
    monkeypatch.setenv(intervals._VIDEO_START_TIMEOUT_ENV, "0.01")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    captured: list[tuple[str, str]] = []
    # `device_probes` hands back its serial, so the recorded pair names the device the probes would
    # have read as well as the trigger that fired.
    monkeypatch.setattr(stall_diagnostics, "device_probes", lambda serial: serial)
    monkeypatch.setattr(
        stall_diagnostics,
        "capture",
        lambda reason, probes: captured.append((probes, reason)),
    )
    device = FakeDevice(pids=["", "1234"], sizes=["0"])  # process appears; the file stays empty

    with caplog.at_level("WARNING"):
        interval = intervals.start_screenrecord(
            "SER",
            tmp_path / "scenario.mp4",
            spawn=lambda a, o: FakeProc(),
            run=device,
            confirm_started=True,
        )

    assert any("produced no new bytes" in r.message for r in caplog.records)
    assert captured == [("SER", "screenrecord-no-growth")]
    # Observational only: the pid confirmation still settled the anchor, and BE-0354's recovery rung
    # reads exactly what it read before. Deciding that a producing-nothing recording should change
    # recovery is a separate question this item deliberately leaves alone.
    assert isinstance(interval.true_start, float)
    assert interval.start_confirmed is True


def test_start_screenrecord_growth_is_confirmed_only_past_the_pre_spawn_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A crash-retry (BE-0049) reuses the scenario id and so the one fixed device-side path, so a
    # finalized earlier attempt's leftover mp4 already has bytes. Without the baseline those bytes
    # would confirm growth that never happened — the same trap the iOS video baseline guards.
    monkeypatch.setenv(intervals._VIDEO_START_TIMEOUT_ENV, "0.01")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(stall_diagnostics, "capture", lambda reason, probes: None)
    device = FakeDevice(pids=["", "1234"], sizes=["4096"])  # leftover bytes, never growing

    with caplog.at_level("WARNING"):
        intervals.start_screenrecord(
            "SER",
            tmp_path / "scenario.mp4",
            spawn=lambda a, o: FakeProc(),
            run=device,
            confirm_started=True,
        )

    assert any("produced no new bytes" in r.message for r in caplog.records)


def test_start_screenrecord_growth_check_is_skipped_when_no_process_appeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # With no process there is nothing to produce bytes, that path already warned, and a second full
    # timeout would buy no new fact — so only the pre-spawn baseline probe should have run.
    monkeypatch.setenv(intervals._VIDEO_START_TIMEOUT_ENV, "0.01")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    captured: list[str] = []
    monkeypatch.setattr(
        stall_diagnostics, "capture", lambda reason, probes: captured.append(reason)
    )
    device = FakeDevice(pids=[""], sizes=["0"])  # the device-side process never appears

    with caplog.at_level("WARNING"):
        interval = intervals.start_screenrecord(
            "SER",
            tmp_path / "scenario.mp4",
            spawn=lambda a, o: FakeProc(),
            run=device,
            confirm_started=True,
        )

    assert interval.true_start is None
    assert len(device.size_probes()) == 1
    assert not any("produced no new bytes" in r.message for r in caplog.records)
    assert captured == []


def test_start_screenrecord_makes_no_size_probe_without_confirm_started(tmp_path: Path) -> None:
    # Every existing caller leaves `confirm_started` off, and must pay no extra device round trip
    # for a check it never asked for.
    device = FakeDevice()
    intervals.start_screenrecord(
        "SER", tmp_path / "scenario.mp4", spawn=lambda a, o: FakeProc(), run=device
    )
    assert device.size_probes() == []


def test_await_screenrecord_growing_returns_as_soon_as_the_file_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real condition wait, not a fixed sleep: growth on the first poll returns immediately, so no
    # clock needs monkeypatching for this to finish.
    device = FakeDevice(sizes=["10"])
    assert intervals._await_screenrecord_growing("SER", device, "/sdcard/x.mp4", 0, timeout=60.0)
    assert len(device.size_probes()) == 1


def test_the_growth_poll_stays_cheap_on_the_launch_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # This poll sits on the critical path — `AndroidEnvironment` prestarts the recording immediately
    # before it launches the app — and every probe is an `adb shell` round trip plus a device-side
    # shell spawn, competing with a cold start on a two-core emulator. So it must stay coarser than
    # its two siblings: the iOS twin reads a host file for free, and the pid confirmation's answer is
    # the video anchor, so its resolution is the measurement. This one answers only yes or no.
    assert intervals._SCREENRECORD_GROWTH_POLL > 0.2
    # And a full stall — the worst case, the only one that pays more than a single probe — must stay
    # in single digits rather than the ~25 round trips a 0.2s cadence would spend. Driven on a fake
    # clock that each `sleep` advances, so this counts the probes the cadence really produces rather
    # than however many a no-op `sleep` would spin through.
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    device = FakeDevice(sizes=["0"])  # never grows, so the poll runs to its deadline

    assert not intervals._await_screenrecord_growing(
        "SER", device, "/sdcard/x.mp4", 0, timeout=intervals._VIDEO_START_TIMEOUT
    )
    assert len(device.size_probes()) <= 10


def test_await_screenrecord_growing_retries_past_a_transient_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stalled device is exactly where the probe itself is most likely to fail transiently, so a
    # failed read is an unmet condition to retry, never a reason to declare the recording dead.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    calls = 0

    def run(argv: list[str]) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.CalledProcessError(1, argv)
        return "512"

    assert intervals._await_screenrecord_growing("SER", run, "/sdcard/x.mp4", 0, timeout=60.0)


def test_screenrecord_baseline_size_discloses_a_failed_probe(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A 0 from a failed probe reads as "no leftover bytes", silently disabling the stale-retry guard
    # the baseline exists for — so it must be said out loud rather than mistimed in silence.
    def run(argv: list[str]) -> str:
        raise subprocess.CalledProcessError(1, argv)

    with caplog.at_level("WARNING"):
        assert intervals._screenrecord_baseline_size("SER", run, "/sdcard/x.mp4") == 0
    assert any("could not size" in r.message for r in caplog.records)


def test_await_screenrecord_started_warns_on_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # If the device-side process never appears, the wait gives up at the deadline rather than hang,
    # leaving true_start unconfirmed — with a warning so a scenario whose video never started is
    # diagnosable rather than silently mistimed.
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def run(argv: list[str]) -> str:
        return ""  # never reports as running

    with caplog.at_level("WARNING"):
        result = intervals._await_screenrecord_started(
            "SER", run, frozenset(), timeout=0.01, poll=0.001
        )
    assert result is None
    assert any("did not appear" in r.message for r in caplog.records)


def test_await_screenrecord_started_retries_past_a_transient_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A transient adb hiccup (device offline for one poll, an adb server restart) must not abort
    # the whole wait the way `_await_screenrecord_stopped` deliberately does — nothing here needs to
    # avoid hanging a pull, so retrying to the deadline is strictly more resilient.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    replies: Iterator[str | Exception] = iter(
        [OSError("adb gone"), "1234"]
    )  # one transient failure, then the real pid

    def run(argv: list[str]) -> str:
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    result = intervals._await_screenrecord_started("SER", run, frozenset(), timeout=1.0, poll=0.001)
    assert isinstance(result, float)


def test_await_screenrecord_started_warns_on_persistent_probe_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A probe that never recovers must still give up at the deadline rather than hang forever, with
    # the same "did not appear" disclosure a plain timeout gets — the caller cannot tell "adb is
    # broken" from "the process never started" apart anyway, and both leave the scenario uncorrected.
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def run(argv: list[str]) -> str:
        raise OSError("adb gone")

    with caplog.at_level("WARNING"):
        result = intervals._await_screenrecord_started(
            "SER", run, frozenset(), timeout=0.01, poll=0.001
        )
    assert result is None
    assert any("did not appear" in r.message for r in caplog.records)


def test_screenrecord_pids_warns_when_the_baseline_probe_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The adb twin of `_file_size(disclose=True)`: an empty baseline reads as "nothing was running
    # device-side", so a failed pre-spawn probe silently disables the leaked-process guard the
    # baseline exists for — it must disclose rather than mistime the scenario silently.
    def run(argv: list[str]) -> str:
        raise OSError("adb gone")

    with caplog.at_level("WARNING"):
        pids = intervals._screenrecord_pids("SER", run)
    assert pids == set()
    assert any("could not probe device-side screenrecord" in r.message for r in caplog.records)


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
    from bajutsu.common.backend_cli import adb

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
    assert json_str(interval["begin"]).startswith("2026-06-05T01:01:11")


def test_parse_app_trace_ignores_unpaired() -> None:
    text = (
        '{"eventType": "logEvent", "eventMessage": "load started",'
        ' "timestamp": "2026-06-05 01:01:11.000000+0900"}'
    )
    assert intervals.parse_app_trace(text) == []


def test_subprocess_proc_closes_file_on_popen_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Popen raises after the output file is opened, the file handle must be closed."""
    import subprocess as sp

    out = tmp_path / "out.log"
    opened_files: list[IO[Any]] = []
    _real_open = Path.open

    def tracking_open(self: Path, *a: Any, **kw: Any) -> IO[Any]:
        f: IO[Any] = _real_open(self, *a, **kw)
        opened_files.append(f)
        return f

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(sp, "Popen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no")))

    with contextlib.suppress(OSError):
        intervals._SubprocessProc(["fake"], out)
    assert len(opened_files) == 1
    assert opened_files[0].closed, "file handle leaked — not closed after Popen failure"


def test_subprocess_proc_kills_after_timeout_when_signal_ignored() -> None:
    # A process that ignores the stop signal is hard-killed once the finalize window elapses, so
    # stop() returns promptly instead of hanging. The window is generous for real video finalize; a
    # tiny one here keeps the test fast while proving the timeout is honored and the kill is the backstop.
    import sys
    import time

    proc = intervals._SubprocessProc(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)",
        ],
        None,
    )
    start = time.monotonic()
    proc.stop(signal.SIGINT, timeout=0.5)
    assert time.monotonic() - start < 10, (
        "stop() should kill after the timeout, not wait out sleep(30)"
    )


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


# --- the recording's own origin, measured from the finished file ---


def _mp4_bytes(seconds: float) -> bytes:
    """A minimal ISO base media file whose movie header states `seconds` of footage."""
    import struct

    def box(kind: bytes, payload: bytes) -> bytes:
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    mvhd = bytes([0]) + b"\0\0\0" + b"\0" * 8 + struct.pack(">II", 1000, round(seconds * 1000))
    return box(b"ftyp", b"isom") + box(b"moov", box(b"mvhd", mvhd + b"\0" * 80))


def test_start_video_stamps_the_span_its_duration_is_checked_against(tmp_path: Path) -> None:
    # `spawned_at` is the one instant that is never in doubt — no confirmation, no signal — which is
    # what makes it the bound a measured duration is sanity-checked against.
    interval = intervals.start_video("UDID", tmp_path / "v.mp4", spawn=lambda argv, out: FakeProc())
    assert isinstance(interval.spawned_at, float)


def test_stop_measures_where_the_footage_begins_from_the_finished_file(tmp_path: Path) -> None:
    # The origin a report seeks against: the recording states its own duration and `stop()` knows
    # when it ended, so the origin is a measurement rather than the start-confirmation proxy — which
    # arrives at whatever distance from the first frame its own signal happens to have. A spawn 2.6s
    # back puts a 2.5s clip's first frame a tenth of a second after it: inside the window a
    # recording can open in, from both sides.
    path = tmp_path / "v.mp4"
    path.write_bytes(_mp4_bytes(2.5))
    interval = intervals.Interval(
        kind="video", path=path, spawned_at=time.monotonic() - 2.6, _proc=FakeProc()
    )
    before = time.monotonic()
    interval.stop()
    assert interval.measured_start is not None
    # 2.5s of footage ending at the stop: the origin is 2.5s before it, give or take the instants
    # this test itself spends around the call.
    assert before - 2.5 - 0.5 < interval.measured_start <= before - 2.5 + 0.5


def test_a_duration_longer_than_the_recording_was_open_for_is_not_a_measurement(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A container written at a nominal frame rate can state more seconds than the recorder ever ran
    # — Playwright's short clips do. Trusting that would place the origin before the recorder
    # existed, which is the near side of the window the spawn bounds, so the proxy stays in charge.
    path = tmp_path / "v.mp4"

    def spawn(argv: list[str], out: Path | None) -> FakeProc:
        path.write_bytes(_mp4_bytes(600.0))
        return FakeProc()

    interval = intervals.start_video("UDID", path, spawn=spawn)
    with caplog.at_level("DEBUG"):
        interval.stop()
    assert interval.measured_start is None
    assert any("outside the window a recording can open in" in r.message for r in caplog.records)


def test_an_unreadable_recording_leaves_the_origin_to_the_start_proxy(tmp_path: Path) -> None:
    # A dropped pull or a truncated finalize must degrade to the behavior every run had before this,
    # never to a guessed origin.
    path = tmp_path / "v.mp4"
    interval = intervals.start_video("UDID", path, spawn=lambda argv, out: FakeProc())
    interval.stop()
    assert interval.measured_start is None


def test_only_a_video_interval_measures_its_origin(tmp_path: Path) -> None:
    # A log stream has no timeline to anchor and no duration to read; asking would just cost a read.
    path = tmp_path / "device.log"
    path.write_bytes(_mp4_bytes(1.0))  # even so shaped, a deviceLog is never measured
    interval = intervals.start_device_log("UDID", path, spawn=lambda argv, out: FakeProc())
    interval.stop()
    assert interval.measured_start is None


def test_the_end_instant_follows_where_the_recording_actually_stops(tmp_path: Path) -> None:
    # Two shapes, one difference: a subprocess recorder stops the moment the signal lands and then
    # spends its finalize writing a clip it already captured, while Playwright films right through
    # the context close `stop()` performs. Run the same slow stop under both flags — the flagged one
    # must place the origin a whole close later, which is exactly the tail the other must not charge
    # to the recording's head.
    close_seconds = 0.05
    slept = 0.0

    class SlowStop:
        def stop(self, sig: int, timeout: float) -> None:
            nonlocal slept
            begin = time.monotonic()
            time.sleep(close_seconds)
            slept = time.monotonic() - begin

    def measured(*, stops_when_stop_returns: bool) -> float:
        path = tmp_path / f"v-{stops_when_stop_returns}.mp4"
        path.write_bytes(_mp4_bytes(1.0))
        interval = intervals.Interval(
            kind="video",
            path=path,
            spawned_at=time.monotonic() - 1.1,
            _proc=SlowStop(),
            stops_when_stop_returns=stops_when_stop_returns,
        )
        interval.stop()
        assert interval.measured_start is not None
        return interval.measured_start - time.monotonic()  # relative, so the two are comparable

    at_signal = measured(stops_when_stop_returns=False)
    # Against the stop that actually happened, not the one asked for: only the unflagged run charges
    # its stop to the tail, and a loaded machine oversleeps well past the 0.05s requested.
    slept_at_signal = slept
    at_return = measured(stops_when_stop_returns=True)
    assert at_return - at_signal == pytest.approx(slept_at_signal, abs=0.03)


def test_adopt_carries_the_span_bound_onto_the_relocated_capture(tmp_path: Path) -> None:
    # `spawned_at` bounds the duration check, and a recording's spawn does not move because its file
    # is later relocated — the same reason `adopt` carries the start confirmation forward.
    running = intervals.Interval(kind="video", path=tmp_path / "tmp.mp4", spawned_at=12.5)
    assert intervals.adopt(running, tmp_path / "final.mp4").spawned_at == 12.5


def test_adopt_measures_the_relocated_file_not_the_temp_one(tmp_path: Path) -> None:
    # Android records to a temp path and the sink adopts it; the wrapper's own stop is the one that
    # sees the finalized file at its artifact path, so that is where the origin is measured from.
    temp = tmp_path / "_tmp" / "prestart-UDID.mp4"
    temp.parent.mkdir()
    temp.write_bytes(_mp4_bytes(1.5))
    running = intervals.Interval(
        kind="video", path=temp, spawned_at=time.monotonic() - 1.6, _proc=FakeProc()
    )

    target = tmp_path / "scenario" / "scenario.mp4"
    adopted = intervals.adopt(running, target)
    before = time.monotonic()
    assert adopted.stop() == target
    assert adopted.measured_start is not None
    assert before - 1.5 - 0.5 < adopted.measured_start <= before - 1.5 + 0.5


def test_a_recorder_that_stopped_itself_is_not_measured(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The opposite failure to an over-long duration, and the one the span bound alone cannot see:
    # Android's `screenrecord` stops at its own time limit, so a scenario outlasting that ceiling
    # signals a recorder that quit long before. The duration is comfortably *under* the span, yet
    # `ended_at` is not when the recording ended, so the origin lands late by that whole gap —
    # silently worse than the proxy it would outrank. Spawned 60s ago with a 1s clip: the first
    # frame would sit 59s after the spawn, which no recorder's startup explains.
    path = tmp_path / "v.mp4"
    path.write_bytes(_mp4_bytes(1.0))
    interval = intervals.Interval(
        kind="video", path=path, spawned_at=time.monotonic() - 60.0, _proc=FakeProc()
    )
    with caplog.at_level("DEBUG"):
        interval.stop()
    assert interval.measured_start is None
    assert any("outside the window a recording can open in" in r.message for r in caplog.records)


def test_a_recording_with_no_spawn_instant_stays_on_the_proxy(tmp_path: Path) -> None:
    # `spawned_at` is what the two inputs are checked against, so a provider that stamps none leaves
    # the origin unvalidated — and an unchecked origin is what the window exists to refuse.
    path = tmp_path / "v.mp4"
    path.write_bytes(_mp4_bytes(1.0))
    interval = intervals.Interval(kind="video", path=path, _proc=FakeProc())
    interval.stop()
    assert interval.measured_start is None

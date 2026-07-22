"""Interval (lifecycle) evidence: video and device logs captured around a step.

These are subprocess child processes started before an action and stopped after the step settles —
`simctl` on iOS, `adb` on Android (the twin providers). Command builders are pure and unit-tested;
process spawning is injected so the start/stop lifecycle is testable without a device. Web is
driver-native and lives in the Playwright driver, not here.

- video: `simctl io <udid> recordVideo` (iOS) / `adb shell screenrecord` (Android) — finalized with
  SIGINT (a hard kill would leave a truncated mp4). Android records device-side and is pulled off
  after stop, since `screenrecord` cannot stream to a host file.
- deviceLog: `simctl spawn <udid> log stream` (iOS) / `adb logcat` (Android) streamed to a file —
  stopped with SIGTERM.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from bajutsu import adb, simctl

_logger = logging.getLogger(__name__)

PROVIDER = "simctl"
ADB_PROVIDER = "adb"


def record_video_cmd(udid: str, path: str) -> list[str]:
    """Build the simctl command that records the screen to `path` (h264)."""
    # Validate the udid inline — as simctl's own builders and adb's `screenrecord_cmd`
    # (via `_checked_serial`) do — so this evidence-capture argv can't carry an option-injecting
    # / metacharacter id even if reached without the earlier `simctl.Env` boundary check.
    return [
        "xcrun",
        "simctl",
        "io",
        simctl.validated_udid(udid),
        "recordVideo",
        "--codec",
        "h264",
        path,
    ]


def device_log_cmd(udid: str, predicate: str | None = None) -> list[str]:
    """Build the simctl command that streams the device log, optionally filtered by `predicate`."""
    cmd = [
        "xcrun",
        "simctl",
        "spawn",
        simctl.validated_udid(udid),
        "log",
        "stream",
        "--level",
        "debug",
        "--style",
        "compact",
    ]
    if predicate:
        cmd += ["--predicate", predicate]
    return cmd


def app_trace_cmd(udid: str, subsystem: str) -> list[str]:
    """Build the simctl command that streams the app's os_log `subsystem` as ndjson."""
    return [
        "xcrun",
        "simctl",
        "spawn",
        simctl.validated_udid(udid),
        "log",
        "stream",
        "--predicate",
        f'subsystem == "{subsystem}"',
        "--style",
        "ndjson",
    ]


# How long `stop()` waits for the signalled process to exit before a hard `kill()`. A log stream ends
# the instant it sees the stop signal, so a short grace is plenty. A screen recording is different: on
# the stop signal `recordVideo` / `screenrecord` still has to flush and mux the whole clip to disk
# ("Writing to disk"), which scales with the recording's length and the host's load. Killing it
# mid-write truncates the mp4 so it has no `moov` atom (unplayable) and — worse on iOS — leaves the
# simulator's host-recording session held, so every later capture fails with "Host recording is
# already in progress". So video gets a generous finalize window; the kill stays only as a last resort.
_STOP_TIMEOUT = 10.0
_VIDEO_FINALIZE_TIMEOUT = 120.0


class Proc(Protocol):
    """A running child process that can be signalled and waited on."""

    def stop(self, sig: int, timeout: float) -> None: ...


# spawn(argv, stdout_path) -> a running process (stdout written to the file if given)
Spawn = Callable[[list[str], "Path | None"], Proc]


class _SubprocessProc:
    def __init__(self, argv: list[str], stdout_path: Path | None) -> None:
        self._file = stdout_path.open("wb") if stdout_path is not None else None
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=self._file if self._file is not None else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except BaseException:
            if self._file is not None:
                self._file.close()
            raise

    def stop(self, sig: int, timeout: float) -> None:
        self._proc.send_signal(sig)
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        if self._file is not None:
            self._file.close()


def _spawn(argv: list[str], stdout_path: Path | None) -> Proc:
    return _SubprocessProc(argv, stdout_path)


class _NullProc:
    """A no-op process (the default so constructing an Interval has no side effects)."""

    def stop(self, sig: int, timeout: float) -> None:
        return None


@dataclass
class Interval:
    """A running interval capture; `stop()` finalizes the artifact file.

    `_transform`, if set, post-processes the captured file after the process stops
    (e.g. parse a raw log stream into a structured trace) and returns the final path.
    """

    kind: str  # "video" | "deviceLog" | "appTrace"
    path: Path
    provider: str = PROVIDER
    _proc: Proc = field(repr=False, default_factory=_NullProc)
    _stop_signal: int = signal.SIGTERM
    _stop_timeout: float = _STOP_TIMEOUT
    _transform: Callable[[Path], Path] | None = field(default=None, repr=False)

    def stop(self) -> Path:
        self._proc.stop(self._stop_signal, self._stop_timeout)
        return self._transform(self.path) if self._transform is not None else self.path


def adopt(interval: Interval, target: Path) -> Interval:
    """Wrap an already-running interval so `stop()` finalizes it, then relocates its file to `target`.

    A device backend starts its video *before* the app launches, so the cold-start frames are
    captured; that recording writes to a temporary path. The sink adopts the running capture at
    scenario start and, on stop, moves the finalized file to the scenario's artifact path — the real
    finalize (the wrapped interval's stop signal and timeout) still runs, this only redirects the
    result. The web lane finalizes in place instead; this is the device twin of that adopt-on-stop
    shape.
    """

    def relocate(_: Path) -> Path:
        finalized = interval.stop()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(finalized), str(target))
        return target

    return Interval(
        kind=interval.kind, path=target, provider=interval.provider, _transform=relocate
    )


def start_video(udid: str, path: Path, spawn: Spawn = _spawn) -> Interval:
    """Begin recording the screen to `path`; stop() (SIGINT) finalizes the mp4.

    The stop gives `recordVideo` the generous `_VIDEO_FINALIZE_TIMEOUT` to write and mux the clip: a
    premature kill would truncate the mp4 (no `moov` atom) and wedge the simulator's recording session.
    """
    proc = spawn(record_video_cmd(udid, str(path)), None)
    return Interval(
        kind="video",
        path=path,
        _proc=proc,
        _stop_signal=signal.SIGINT,
        _stop_timeout=_VIDEO_FINALIZE_TIMEOUT,
    )


def start_device_log(
    udid: str, path: Path, predicate: str | None = None, spawn: Spawn = _spawn
) -> Interval:
    """Begin streaming the device log to `path`; stop() (SIGTERM) ends the stream."""
    proc = spawn(device_log_cmd(udid, predicate), path)
    return Interval(kind="deviceLog", path=path, _proc=proc, _stop_signal=signal.SIGTERM)


# --- adb (Android) interval providers: the twins of the simctl starters above ---


def _await_screenrecord_stopped(
    serial: str, run: adb.RunFn, timeout: float = _VIDEO_FINALIZE_TIMEOUT, poll: float = 0.2
) -> None:
    """Wait until the device-side `screenrecord` has exited, before its mp4 is pulled.

    On the stop signal the local `adb shell` client returns as soon as the connection closes, but the
    device-side `screenrecord` is still writing the mp4's `moov` atom. Pulling then races that write
    and yields a truncated, moov-less (unplayable) file — the Android recording instability. Poll to a
    bounded deadline (a condition wait on the process's exit, not a fixed sleep). If the probe can't
    run or never clears, proceed anyway so it can never hang the run — the pull stays best-effort —
    but log a warning: the pull may then copy a still-finalizing (truncated, moov-less) mp4, the very
    failure this wait exists to prevent, so it must not be silent.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not run(adb.screenrecord_pids_cmd(serial)).strip():
                return
        except (subprocess.CalledProcessError, OSError) as exc:
            _logger.warning(
                "could not probe device-side screenrecord (%s); pulling anyway — the video may be "
                "truncated (no moov atom)",
                exc,
            )
            return
        time.sleep(poll)
    _logger.warning(
        "device-side screenrecord still running after %ss; pulling anyway — the video may be "
        "truncated (no moov atom)",
        timeout,
    )


def start_screenrecord(
    serial: str, path: Path, spawn: Spawn = _spawn, run: adb.RunFn = adb._real_run
) -> Interval:
    """Record the Android screen; stop() (SIGINT) finalizes the mp4, then pulls it off the device.

    `screenrecord` writes device-side (it cannot stream to a host file), so recording is a running
    process plus a post-stop transform: wait for the device-side finalize, pull the mp4 to `path`,
    then remove it device-side. SIGINT (not a kill) lets `screenrecord` flush a complete mp4, the same
    reason simctl recordVideo finalizes on SIGINT. Two waits guard the finalize: `stop()` gives the
    *local* `adb shell` client `_VIDEO_FINALIZE_TIMEOUT` before any hard kill, then the transform waits
    for the *device-side* `screenrecord` to exit (`_await_screenrecord_stopped`) — the local client
    returns before the device finishes writing the moov atom, so pulling without that wait races the
    finalize into a truncated, unplayable file.
    """
    device_path = adb.VIDEO_DEVICE_PATH
    proc = spawn(adb.screenrecord_cmd(serial, device_path), None)

    def transform(target: Path) -> Path:
        # The local `adb shell` has returned, but the device-side screenrecord is still finalizing;
        # wait for it to exit so the pull gets a complete mp4 rather than a moov-less truncation.
        _await_screenrecord_stopped(serial, run)
        # Let a failed pull surface (like the iOS video provider): swallowing it would record a video
        # artifact path with no file behind it, turning a real problem into a silent one.
        run(adb.pull_cmd(serial, device_path, str(target)))
        # The recording is pulled; a failed cleanup of the device copy must not fail the run.
        with contextlib.suppress(subprocess.CalledProcessError, OSError):
            run(adb.rm_cmd(serial, device_path))
        return target

    return Interval(
        kind="video",
        path=path,
        provider=ADB_PROVIDER,
        _proc=proc,
        _stop_signal=signal.SIGINT,
        _stop_timeout=_VIDEO_FINALIZE_TIMEOUT,
        _transform=transform,
    )


def start_logcat(serial: str, path: Path, spawn: Spawn = _spawn) -> Interval:
    """Begin streaming `adb logcat` to `path`; stop() (SIGTERM) ends the stream (the deviceLog twin)."""
    proc = spawn(adb.logcat_cmd(serial), path)
    return Interval(
        kind="deviceLog", path=path, provider=ADB_PROVIDER, _proc=proc, _stop_signal=signal.SIGTERM
    )


# --- appTrace: pair start/finish log markers into timed intervals ---
#
# os_signpost intervals are meant for Instruments and do not show up in `log stream`,
# so appTrace works off ordinary log markers: a message "<name> started" opens an
# interval and "<name> finished" (or ended/done) closes it, timed by the log stamps.

_BEGIN = re.compile(r"^(?P<name>.+?) (?:started|begin)$")
_END = re.compile(r"^(?P<name>.+?) (?:finished|ended|done|end)$")


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f%z")
    except ValueError:
        return None


def parse_app_trace(ndjson_text: str) -> list[dict[str, object]]:
    """Pair '<name> started' / '<name> finished' log lines into timed intervals."""
    begins: dict[str, datetime] = {}
    out: list[dict[str, object]] = []
    for line in ndjson_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("eventType") != "logEvent":
            continue
        message = str(event.get("eventMessage") or "").strip()
        stamp = _parse_ts(event.get("timestamp"))
        if stamp is None:
            continue
        begin = _BEGIN.match(message)
        if begin:
            begins[begin.group("name")] = stamp
            continue
        end = _END.match(message)
        if end and end.group("name") in begins:
            start = begins.pop(end.group("name"))
            out.append(
                {
                    "name": end.group("name"),
                    "begin": start.isoformat(),
                    "end": stamp.isoformat(),
                    "durationMs": round((stamp - start).total_seconds() * 1000, 1),
                }
            )
    return out


def start_app_trace(
    udid: str, raw_path: Path, json_path: Path, subsystem: str, spawn: Spawn = _spawn
) -> Interval:
    """Stream the app's logs to raw_path; on stop, write the parsed trace to json_path."""
    proc = spawn(app_trace_cmd(udid, subsystem), raw_path)

    def transform(raw: Path) -> Path:
        text = raw.read_text(encoding="utf-8", errors="ignore") if raw.exists() else ""
        json_path.write_text(json.dumps(parse_app_trace(text), indent=2), encoding="utf-8")
        return json_path

    return Interval(
        kind="appTrace",
        path=raw_path,
        _proc=proc,
        _stop_signal=signal.SIGTERM,
        _transform=transform,
    )


# kind -> starter, for the sink to dispatch on a capture token's kind
STARTERS: dict[str, Callable[..., Interval]] = {
    "video": start_video,
    "deviceLog": start_device_log,
}
INTERVAL_KINDS = frozenset({*STARTERS, "appTrace"})

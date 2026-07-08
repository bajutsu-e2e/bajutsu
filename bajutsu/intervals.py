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
import re
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from bajutsu import adb

PROVIDER = "simctl"
ADB_PROVIDER = "adb"


def record_video_cmd(udid: str, path: str) -> list[str]:
    """Build the simctl command that records the screen to `path` (h264)."""
    return ["xcrun", "simctl", "io", udid, "recordVideo", "--codec", "h264", path]


def device_log_cmd(udid: str, predicate: str | None = None) -> list[str]:
    """Build the simctl command that streams the device log, optionally filtered by `predicate`."""
    cmd = [
        "xcrun",
        "simctl",
        "spawn",
        udid,
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
        udid,
        "log",
        "stream",
        "--predicate",
        f'subsystem == "{subsystem}"',
        "--style",
        "ndjson",
    ]


class Proc(Protocol):
    """A running child process that can be signalled and waited on."""

    def stop(self, sig: int) -> None: ...


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

    def stop(self, sig: int) -> None:
        self._proc.send_signal(sig)
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        if self._file is not None:
            self._file.close()


def _spawn(argv: list[str], stdout_path: Path | None) -> Proc:
    return _SubprocessProc(argv, stdout_path)


class _NullProc:
    """A no-op process (the default so constructing an Interval has no side effects)."""

    def stop(self, sig: int) -> None:
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
    _transform: Callable[[Path], Path] | None = field(default=None, repr=False)

    def stop(self) -> Path:
        self._proc.stop(self._stop_signal)
        return self._transform(self.path) if self._transform is not None else self.path


def start_video(udid: str, path: Path, spawn: Spawn = _spawn) -> Interval:
    """Begin recording the screen to `path`; stop() (SIGINT) finalizes the mp4."""
    proc = spawn(record_video_cmd(udid, str(path)), None)
    return Interval(kind="video", path=path, _proc=proc, _stop_signal=signal.SIGINT)


def start_device_log(
    udid: str, path: Path, predicate: str | None = None, spawn: Spawn = _spawn
) -> Interval:
    """Begin streaming the device log to `path`; stop() (SIGTERM) ends the stream."""
    proc = spawn(device_log_cmd(udid, predicate), path)
    return Interval(kind="deviceLog", path=path, _proc=proc, _stop_signal=signal.SIGTERM)


# --- adb (Android) interval providers: the twins of the simctl starters above ---


def start_screenrecord(
    serial: str, path: Path, spawn: Spawn = _spawn, run: adb.RunFn = adb._real_run
) -> Interval:
    """Record the Android screen; stop() (SIGINT) finalizes the mp4, then pulls it off the device.

    `screenrecord` writes device-side (it cannot stream to a host file), so recording is a running
    process plus a post-stop transform: pull the finalized mp4 to `path`, then remove it device-side.
    SIGINT (not a kill) lets `screenrecord` flush a complete mp4, the same reason simctl recordVideo
    finalizes on SIGINT.
    """
    device_path = adb.VIDEO_DEVICE_PATH
    proc = spawn(adb.screenrecord_cmd(serial, device_path), None)

    def transform(target: Path) -> Path:
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

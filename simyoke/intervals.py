"""Interval (lifecycle) evidence: video and device logs captured around a step.

Both are backend-independent (simctl) child processes: started before an action and
stopped after the step settles. Command builders are pure and unit-tested; process
spawning is injected so the start/stop lifecycle is testable without a device.

- video: `simctl io <udid> recordVideo` — finalized with SIGINT (a hard kill would
  leave a truncated mp4).
- deviceLog: `simctl spawn <udid> log stream` streamed to a file — stopped with SIGTERM.
"""

from __future__ import annotations

import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

PROVIDER = "simctl"


def record_video_cmd(udid: str, path: str) -> list[str]:
    return ["xcrun", "simctl", "io", udid, "recordVideo", "--codec", "h264", path]


def device_log_cmd(udid: str, predicate: str | None = None) -> list[str]:
    cmd = ["xcrun", "simctl", "spawn", udid, "log", "stream", "--level", "debug", "--style", "compact"]
    if predicate:
        cmd += ["--predicate", predicate]
    return cmd


class Proc(Protocol):
    """A running child process that can be signalled and waited on."""

    def stop(self, sig: int) -> None: ...


# spawn(argv, stdout_path) -> a running process (stdout written to the file if given)
Spawn = Callable[[list[str], "Path | None"], Proc]


class _SubprocessProc:
    def __init__(self, argv: list[str], stdout_path: Path | None) -> None:
        self._file = stdout_path.open("wb") if stdout_path is not None else None
        self._proc = subprocess.Popen(
            argv,
            stdout=self._file if self._file is not None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

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
    """A running interval capture; `stop()` finalizes the artifact file."""

    kind: str  # "video" | "deviceLog"
    path: Path
    provider: str = PROVIDER
    _proc: Proc = field(repr=False, default_factory=_NullProc)
    _stop_signal: int = signal.SIGTERM

    def stop(self) -> Path:
        self._proc.stop(self._stop_signal)
        return self.path


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


# kind -> starter, for the sink to dispatch on a capture token's kind
STARTERS: dict[str, Callable[..., Interval]] = {
    "video": start_video,
    "deviceLog": start_device_log,
}
INTERVAL_KINDS = frozenset(STARTERS)

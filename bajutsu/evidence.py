"""Evidence capture: instant artifacts (screenshot / elements) plus interval
artifacts (video / deviceLog) captured around a step.

Instant captures are written after a step; interval captures are started before
the action and stopped after the step settles (see `bajutsu.intervals`). Every
artifact records its provider so the manifest shows where it came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bajutsu import intervals
from bajutsu.drivers import base

# step-dir file names for interval kinds
_INTERVAL_FILE = {"video": "segment.mp4", "deviceLog": "device.log"}


@dataclass
class Artifact:
    """One captured file, tagged with how it was produced (manifest provenance)."""

    name: str
    kind: str
    provider: str


def write_elements(driver: base.Driver, step_dir: Path) -> Path:
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / "elements.json"
    path.write_text(
        json.dumps(driver.query(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_screenshot(driver: base.Driver, step_dir: Path, name: str = "after.png") -> Path:
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / name
    driver.screenshot(str(path))
    return path


def capture(driver: base.Driver, step_dir: Path, kinds: list[str]) -> list[Artifact]:
    """Capture the requested instant kinds; return their artifact records."""
    out: list[Artifact] = []
    for token in kinds:
        kind, _, modifier = token.partition(".")
        if kind == "elements":
            out.append(Artifact(write_elements(driver, step_dir).name, "elements", "driver"))
        elif kind == "screenshot":
            name = f"{modifier or 'after'}.png"
            out.append(Artifact(write_screenshot(driver, step_dir, name).name, "screenshot", "driver"))
        # actionLog lives in the manifest; video / deviceLog are intervals (start_intervals).
    return out


class EvidenceSink(Protocol):
    """Where fired captures go. The orchestrator starts intervals before a step and
    captures instant artifacts after it."""

    def start_intervals(self, step_id: str, kinds: list[str]) -> list[intervals.Interval]: ...
    def capture(self, driver: base.Driver, step_id: str, kinds: list[str]) -> list[Artifact]: ...


class NullSink:
    """Default sink: capture nothing (keeps runs side-effect free unless asked)."""

    def start_intervals(self, step_id: str, kinds: list[str]) -> list[intervals.Interval]:
        return []

    def capture(self, driver: base.Driver, step_id: str, kinds: list[str]) -> list[Artifact]:
        return []


class FileSink:
    """Write captured artifacts under run_dir/<step_id>/.

    `udid` is needed for interval captures (simctl video / log); without it they are
    skipped. `log_predicate` narrows the device-log stream (e.g. by subsystem).
    """

    def __init__(
        self,
        run_dir: Path,
        udid: str | None = None,
        log_predicate: str | None = None,
        log_subsystem: str | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.udid = udid
        self.log_predicate = log_predicate
        self.log_subsystem = log_subsystem  # for appTrace: the app's os_log subsystem

    def start_intervals(self, step_id: str, kinds: list[str]) -> list[intervals.Interval]:
        if self.udid is None or not kinds:
            return []
        step_dir = self.run_dir / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        started: list[intervals.Interval] = []
        for token in kinds:
            kind = token.partition(".")[0]
            path = step_dir / _INTERVAL_FILE.get(kind, f"{kind}.bin")
            if kind == "video":
                started.append(intervals.start_video(self.udid, path))
            elif kind == "deviceLog":
                started.append(intervals.start_device_log(self.udid, path, self.log_predicate))
            elif kind == "appTrace" and self.log_subsystem:
                started.append(intervals.start_app_trace(
                    self.udid, step_dir / "appTrace.raw", step_dir / "appTrace.json",
                    self.log_subsystem,
                ))
        return started

    def capture(self, driver: base.Driver, step_id: str, kinds: list[str]) -> list[Artifact]:
        return capture(driver, self.run_dir / step_id, kinds)

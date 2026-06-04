"""Evidence capture: instant artifacts (screenshot / elements) written after each
step, plus interval artifacts (video / deviceLog / appTrace) recorded for the whole
scenario.

Instant captures land in run_dir/<step_id>/; interval captures run for the whole
scenario and land in run_dir/<scenario_id>/. Every artifact records its provider so
the manifest shows where it came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bajutsu import intervals
from bajutsu.drivers import base
from bajutsu.redaction import Redactor
from bajutsu.scenario import Redact

# scenario-dir file names for interval kinds
_INTERVAL_FILE = {"video": "scenario.mp4", "deviceLog": "device.log"}


@dataclass
class Artifact:
    """One captured file, tagged with how it was produced (manifest provenance)."""

    name: str
    kind: str
    provider: str


def write_elements(driver: base.Driver, step_dir: Path, redactor: Redactor | None = None) -> Path:
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / "elements.json"
    elements = driver.query()
    if redactor is not None:
        elements = redactor.redact_elements(elements)
    path.write_text(
        json.dumps(elements, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_screenshot(driver: base.Driver, step_dir: Path, name: str = "after.png") -> Path:
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / name
    driver.screenshot(str(path))
    return path


def capture(
    driver: base.Driver, step_dir: Path, kinds: list[str], redactor: Redactor | None = None
) -> list[Artifact]:
    """Capture the requested instant kinds; return their artifact records."""
    out: list[Artifact] = []
    for token in kinds:
        kind, _, modifier = token.partition(".")
        if kind == "elements":
            out.append(Artifact(write_elements(driver, step_dir, redactor).name, "elements", "driver"))
        elif kind == "screenshot":
            name = f"{modifier or 'after'}.png"
            out.append(Artifact(write_screenshot(driver, step_dir, name).name, "screenshot", "driver"))
        # actionLog lives in the manifest; video / deviceLog / appTrace are intervals.
    return out


class EvidenceSink(Protocol):
    """Where evidence goes. The orchestrator captures instant artifacts after each
    step, and records the interval artifacts (video / deviceLog / appTrace) for the
    whole scenario."""

    def capture(self, driver: base.Driver, step_id: str, kinds: list[str]) -> list[Artifact]: ...
    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]: ...
    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]: ...


class NullSink:
    """Default sink: capture nothing (keeps runs side-effect free unless asked)."""

    def capture(self, driver: base.Driver, step_id: str, kinds: list[str]) -> list[Artifact]:
        return []

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        return []

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        return []


class FileSink:
    """Write instant artifacts under run_dir/<step_id>/ and the scenario's interval
    recordings under run_dir/<scenario_id>/.

    `udid` is needed for interval captures (simctl video / log); without it they are
    skipped. `log_predicate` narrows the device-log stream (e.g. by subsystem);
    `log_subsystem` is the app's os_log subsystem for appTrace.
    """

    def __init__(
        self,
        run_dir: Path,
        udid: str | None = None,
        log_predicate: str | None = None,
        log_subsystem: str | None = None,
        redact: Redact | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.udid = udid
        self.log_predicate = log_predicate
        self.log_subsystem = log_subsystem  # for appTrace: the app's os_log subsystem
        self.redactor = Redactor(redact)

    def capture(self, driver: base.Driver, step_id: str, kinds: list[str]) -> list[Artifact]:
        # Re-root each artifact name under step_id so it is relative to the run dir
        # (e.g. "00-slug/step0/after.png") and the HTML report can reference it.
        arts = capture(driver, self.run_dir / step_id, kinds, self.redactor)
        return [Artifact(f"{step_id}/{a.name}", a.kind, a.provider) for a in arts]

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        """Start the whole-scenario recordings under <scenario_id>/ (needs a udid)."""
        if self.udid is None or not kinds:
            return []
        scenario_dir = self.run_dir / scenario_id
        scenario_dir.mkdir(parents=True, exist_ok=True)
        started: list[intervals.Interval] = []
        for token in kinds:
            kind = token.partition(".")[0]
            if kind == "video":
                started.append(intervals.start_video(self.udid, scenario_dir / "scenario.mp4"))
            elif kind == "deviceLog":
                started.append(intervals.start_device_log(
                    self.udid, scenario_dir / "device.log", self.log_predicate))
            elif kind == "appTrace" and self.log_subsystem:
                started.append(intervals.start_app_trace(
                    self.udid, scenario_dir / "appTrace.raw", scenario_dir / "appTrace.json",
                    self.log_subsystem,
                ))
        return started

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        """Finalize each recording; artifact names are relative to the run dir so the
        HTML report (written there) can link/embed them directly."""
        out: list[Artifact] = []
        for interval in started:
            path = interval.stop()
            self._redact_file(path)
            if interval.kind == "appTrace":
                self._redact_file(path.parent / "appTrace.raw")  # scrub the raw stream too
            try:
                name = str(path.relative_to(self.run_dir))
            except ValueError:
                name = path.name
            out.append(Artifact(name=name, kind=interval.kind, provider=interval.provider))
        return out

    def _redact_file(self, path: Path) -> None:
        """Scrub secrets from a text evidence file in place (images are skipped)."""
        if not self.redactor.active or path.suffix == ".mp4" or not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        redacted = self.redactor.redact_text(text)
        if redacted != text:
            path.write_text(redacted, encoding="utf-8")

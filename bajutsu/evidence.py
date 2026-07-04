"""Evidence capture: instant and interval artifacts written during a run.

Instant artifacts (screenshot / elements) are written after each step; interval
artifacts (video / deviceLog / appTrace) are recorded for the whole scenario.
Instant captures land in run_dir/<step_id>/; interval captures run for the whole
scenario and land in run_dir/<scenario_id>/. Every artifact records its provider so
the manifest shows where it came from.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bajutsu import intervals
from bajutsu.artifact_perms import restrict_file
from bajutsu.drivers import base
from bajutsu.redaction import Redactor
from bajutsu.scenario import Redact

_logger = logging.getLogger(__name__)

# scenario-dir file names for interval kinds — one source of truth for both the simctl (iOS)
# and the Playwright (web) providers, so the two never drift.
_INTERVAL_FILE = {"video": "scenario.mp4", "deviceLog": "device.log", "appTrace": "appTrace.raw"}


def _interval_filename(kind: str) -> str:
    """The artifact filename for an interval `kind`."""
    return _INTERVAL_FILE.get(kind, kind)


@dataclass
class Artifact:
    """One captured file, tagged with how it was produced (manifest provenance)."""

    name: str
    kind: str
    provider: str


def write_elements(
    driver: base.Driver,
    step_dir: Path,
    redactor: Redactor | None = None,
    *,
    elements: list[base.Element] | None = None,
    mkdir: bool = True,
) -> Path:
    """Write the element tree (redacted if a redactor is given) to elements.json.

    Uses `elements` if given, otherwise queries the driver now. `mkdir` creates the
    step dir first, and is skipped when the caller already made it.
    """
    if mkdir:
        step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / "elements.json"
    els = elements if elements is not None else driver.query()
    if redactor is not None:
        els = redactor.redact_elements(els)
    path.write_text(
        json.dumps(els, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_screenshot(
    driver: base.Driver, step_dir: Path, name: str = "after.png", *, mkdir: bool = True
) -> Path:
    """Write a screenshot to the step dir.

    `mkdir` creates the step dir first, and is skipped when the caller already made it.
    """
    if mkdir:
        step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / name
    driver.screenshot(str(path))
    # A screenshot can capture on-screen secrets — lock it to the owner regardless of umask (BE-0131).
    restrict_file(path)
    return path


def capture(
    driver: base.Driver,
    step_dir: Path,
    kinds: list[str],
    redactor: Redactor | None = None,
    *,
    elements: list[base.Element] | None = None,
) -> list[Artifact]:
    """Capture the requested instant kinds; return their artifact records."""
    # Create the step dir once here, only for kinds we actually write, so the per-kind
    # writers can skip their own mkdir (every kind targets the same step_dir, so repeating
    # it per writer is wasted syscalls); unmatched-only kinds leave the dir untouched as before.
    if any(token.partition(".")[0] in ("elements", "screenshot") for token in kinds):
        step_dir.mkdir(parents=True, exist_ok=True)
    out: list[Artifact] = []
    for token in kinds:
        kind, _, modifier = token.partition(".")
        if kind == "elements":
            out.append(
                Artifact(
                    write_elements(driver, step_dir, redactor, elements=elements, mkdir=False).name,
                    "elements",
                    "driver",
                )
            )
        elif kind == "screenshot":
            name = f"{modifier or 'after'}.png"
            out.append(
                Artifact(
                    write_screenshot(driver, step_dir, name, mkdir=False).name,
                    "screenshot",
                    "driver",
                )
            )
        # actionLog lives in the manifest; video / deviceLog / appTrace are intervals.
    return out


class EvidenceSink(Protocol):
    """Where evidence goes during a run.

    The orchestrator captures instant artifacts after each step, and records the
    interval artifacts (video / deviceLog / appTrace) for the whole scenario.
    """

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
    ) -> list[Artifact]: ...
    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]: ...
    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]: ...


class NullSink:
    """Default sink: capture nothing (keeps runs side-effect free unless asked)."""

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
    ) -> list[Artifact]:
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
    """Write artifacts to disk under the run dir.

    Instant artifacts go under run_dir/<step_id>/ and the scenario's interval
    recordings under run_dir/<scenario_id>/. `udid` is needed for interval captures
    (simctl video / log); without it they are skipped. `log_predicate` narrows the
    device-log stream (e.g. by subsystem); `log_subsystem` is the app's os_log
    subsystem for appTrace.
    """

    def __init__(
        self,
        run_dir: Path,
        udid: str | None = None,
        log_predicate: str | None = None,
        log_subsystem: str | None = None,
        redact: Redact | None = None,
        secrets: list[str] | None = None,
        web_interval: Callable[[str, Path], intervals.Interval | None] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.udid = udid
        self.log_predicate = log_predicate
        self.log_subsystem = log_subsystem  # for appTrace: the app's os_log subsystem
        self.redactor = Redactor(redact, values=secrets)
        # When set (a web lane), interval evidence comes from this Playwright-native provider
        # instead of the simctl starters below — the device pool injects the driver's `web_interval`.
        self.web_interval = web_interval

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
    ) -> list[Artifact]:
        # Re-root each artifact name under step_id so it is relative to the run dir
        # (e.g. "00-slug/step0/after.png") and the HTML report can reference it.
        arts = capture(driver, self.run_dir / step_id, kinds, self.redactor, elements=elements)
        return [Artifact(f"{step_id}/{a.name}", a.kind, a.provider) for a in arts]

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        """Start the whole-scenario recordings under <scenario_id>/.

        A web lane records via the injected `web_interval` provider (Playwright-native); otherwise
        the simctl starters drive iOS, which need a `udid`.
        """
        if not kinds:
            return []
        scenario_dir = self.run_dir / scenario_id
        if self.web_interval is not None:
            scenario_dir.mkdir(parents=True, exist_ok=True)
            web_started: list[intervals.Interval] = []
            for token in kinds:
                kind = token.partition(".")[0]
                interval = self.web_interval(kind, scenario_dir / _interval_filename(kind))
                if interval is not None:
                    web_started.append(interval)
            return web_started
        if self.udid is None:
            return []
        scenario_dir.mkdir(parents=True, exist_ok=True)
        started: list[intervals.Interval] = []
        for token in kinds:
            kind = token.partition(".")[0]
            if kind == "video":
                started.append(
                    intervals.start_video(self.udid, scenario_dir / _interval_filename("video"))
                )
            elif kind == "deviceLog":
                started.append(
                    intervals.start_device_log(
                        self.udid,
                        scenario_dir / _interval_filename("deviceLog"),
                        self.log_predicate,
                    )
                )
            elif kind == "appTrace" and self.log_subsystem:
                started.append(
                    intervals.start_app_trace(
                        self.udid,
                        scenario_dir / _interval_filename("appTrace"),
                        scenario_dir / "appTrace.json",
                        self.log_subsystem,
                    )
                )
        return started

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        """Finalize each recording into an artifact.

        Artifact names are relative to the run dir so the HTML report (written there)
        can link/embed them directly.
        """
        out: list[Artifact] = []
        for interval in started:
            path = interval.stop()
            # appTrace also has a raw stream beside it; both must be scrubbed before the artifact ships.
            to_scrub = [path]
            if interval.kind == "appTrace":
                to_scrub.append(path.parent / "appTrace.raw")
            unsafe = [p for p in to_scrub if not self._redact_file(p)]
            if unsafe:
                # Redaction is a security control: if we couldn't read a file to scrub it, don't ship
                # the artifact (fail closed), and name the offending file loudly rather than leak it.
                _logger.warning(
                    "dropping %s evidence: could not read %s to redact secrets (failing closed)",
                    interval.kind,
                    ", ".join(str(p) for p in unsafe),
                )
                continue
            try:
                name = str(path.relative_to(self.run_dir))
            except ValueError:
                name = path.name
            out.append(Artifact(name=name, kind=interval.kind, provider=interval.provider))
        return out

    def _redact_file(self, path: Path) -> bool:
        """Scrub secrets from a text evidence file in place; return whether it is safe to ship.

        Safe (True) means there is nothing left to leak: the file was scrubbed, or there was nothing
        to redact (no active redactor, a video, or a missing file). Unsafe (False) means an active
        redactor could not read the file, so the caller must not emit it.
        """
        if not self.redactor.active or path.suffix == ".mp4" or not path.exists():
            return True
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        redacted = self.redactor.redact_text(text)
        if redacted != text:
            path.write_text(redacted, encoding="utf-8")
        return True

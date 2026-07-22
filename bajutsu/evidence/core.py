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
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from bajutsu.artifact_perms import restrict_file
from bajutsu.drivers import base
from bajutsu.evidence import intervals
from bajutsu.evidence.redaction import Redactor
from bajutsu.scenario import Redact

if TYPE_CHECKING:
    # Imported for typing only — importing at runtime would cycle (orchestrator imports this module).
    # The writer reads these by attribute, so it needs no runtime import.
    from bajutsu.orchestrator.waits import WaitTrace
    from bajutsu.platform_lifecycle import ReadinessResult

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
    # The element dump holds on-screen text (redacted best-effort) — owner-only, umask-independent (BE-0131).
    restrict_file(path)
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


def write_wait_diagnostic(
    step_dir: Path,
    *,
    trace: WaitTrace,
    elements: list[base.Element],
    readiness: ReadinessResult | None,
    provenance: Mapping[str, object] | None,
    redactor: Redactor | None = None,
    mkdir: bool = True,
) -> Path:
    """Write a `for`-wait timeout diagnostic (redacted tree + readiness + trace + provenance).

    Everything needed to decide *why* a first `wait` timed out, in one self-contained file so a
    rerun-to-green does not discard the evidence (BE-0231 Unit 1). `awaitedEverQueryable` is always
    false: a `for` wait returns the instant the element matches, so a timeout means it was never
    queryable across the recorded polls. Pure diagnosis — never a verdict input (prime directive 1).
    """
    if mkdir:
        step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / "wait-timeout.json"
    els = redactor.redact_elements(elements) if redactor is not None else elements
    doc = {
        "target": trace.target,
        "timeoutSeconds": trace.timeout_s,
        "readiness": (
            None
            if readiness is None
            else {
                "ready": readiness.ready,
                "signal": readiness.signal,
                "elapsedSeconds": readiness.elapsed_s,
            }
        ),
        "trace": {
            "polls": trace.polls,
            "firstNonemptySeconds": trace.first_nonempty_s,
            "elementsAtTimeout": trace.elements_at_timeout,
            "awaitedEverQueryable": False,
        },
        "provenance": dict(provenance) if provenance is not None else None,
        "elements": els,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    # Holds on-screen text (redacted best-effort) — owner-only, umask-independent (BE-0131).
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
    def wait_diagnostic(
        self,
        step_id: str,
        *,
        trace: WaitTrace,
        elements: list[base.Element],
    ) -> Artifact | None: ...
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

    def wait_diagnostic(
        self,
        step_id: str,
        *,
        trace: WaitTrace,
        elements: list[base.Element],
    ) -> Artifact | None:
        return None

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
    recordings under run_dir/<scenario_id>/. Interval captures come from the driver
    (`driver_interval`, web / Android) when it supplies one, else the simctl path,
    which needs a `udid`; without either they are skipped. `log_predicate` narrows the
    simctl device-log stream (e.g. by subsystem); `log_subsystem` is the app's os_log
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
        driver_interval: Callable[[str, Path], intervals.Interval | None] | None = None,
        prestarted_intervals: list[intervals.Interval] | None = None,
        readiness: ReadinessResult | None = None,
        provenance: Mapping[str, object] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.udid = udid
        self.log_predicate = log_predicate
        self.log_subsystem = log_subsystem  # for appTrace: the app's os_log subsystem
        self.redactor = Redactor(redact, values=secrets)
        # When set (a web or Android lane), interval evidence comes from this driver-supplied provider
        # instead of the simctl starters below — the device pool injects the driver's `driver_interval`.
        self.driver_interval = driver_interval
        # Captures the environment already began before the app launched (a device backend's video, so
        # the cold-start frames are recorded); the sink adopts the running one at scenario start rather
        # than starting a fresh one on demand. Keyed by kind — at most one per kind.
        self._prestarted = {iv.kind: iv for iv in (prestarted_intervals or [])}
        # The launch readiness outcome and the run's BE-0049 provenance, folded into a first-wait
        # timeout diagnostic so the failure is decidable from artifacts alone (BE-0231 Unit 1).
        self.readiness = readiness
        self.provenance = provenance

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

    def wait_diagnostic(
        self,
        step_id: str,
        *,
        trace: WaitTrace,
        elements: list[base.Element],
    ) -> Artifact | None:
        """Write the first-wait timeout diagnostic under <step_id>/ and record it as an artifact."""
        path = write_wait_diagnostic(
            self.run_dir / step_id,
            trace=trace,
            elements=elements,
            readiness=self.readiness,
            provenance=self.provenance,
            redactor=self.redactor,
        )
        return Artifact(f"{step_id}/{path.name}", "waitDiagnostic", "runner")

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        """Start the whole-scenario recordings under <scenario_id>/.

        A kind the environment already began before launch (a device backend's video, `_prestarted`)
        is adopted rather than started, so the recording spans the app launch; the finalized file is
        relocated here on stop. Otherwise a driver-supplied lane records via the injected
        `driver_interval` provider (Playwright-native on web, adb `screenrecord`/`logcat` on Android),
        and failing that the simctl starters drive iOS, which need a `udid`.
        """
        if not kinds:
            return []
        if not (self._prestarted or self.driver_interval is not None or self.udid is not None):
            return []  # no lane can record: skip without creating an empty scenario dir
        scenario_dir = self.run_dir / scenario_id
        scenario_dir.mkdir(parents=True, exist_ok=True)
        started: list[intervals.Interval] = []
        for token in kinds:
            kind = token.partition(".")[0]
            target = scenario_dir / _interval_filename(kind)
            pre = self._prestarted.get(kind)
            if pre is not None:
                started.append(intervals.adopt(pre, target))
            elif self.driver_interval is not None:
                # A driver-supplied lane owns every kind it records; one it declines (None) is simply
                # absent — it must never fall through to the simctl starters (they would run against a
                # non-simctl device, e.g. an Android serial).
                interval = self.driver_interval(kind, target)
                if interval is not None:
                    started.append(interval)
            elif self.udid is not None:
                interval = self._start_simctl_interval(kind, target, scenario_dir)
                if interval is not None:
                    started.append(interval)
        return started

    def _start_simctl_interval(
        self, kind: str, target: Path, scenario_dir: Path
    ) -> intervals.Interval | None:
        """Start one simctl interval capture (iOS), or None for a kind this lane does not record."""
        assert self.udid is not None
        if kind == "video":
            return intervals.start_video(self.udid, target)
        if kind == "deviceLog":
            return intervals.start_device_log(self.udid, target, self.log_predicate)
        if kind == "appTrace" and self.log_subsystem:
            return intervals.start_app_trace(
                self.udid, target, scenario_dir / "appTrace.json", self.log_subsystem
            )
        return None

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        """Finalize each recording into an artifact.

        Artifact names are relative to the run dir so the HTML report (written there)
        can link/embed them directly.
        """
        out: list[Artifact] = []
        for interval in started:
            try:
                path = interval.stop()
            except (subprocess.CalledProcessError, OSError) as exc:
                # An I/O failure while finalizing (e.g. the adb `screenrecord` pull raising when the
                # device vanished) drops just this artifact rather than aborting the loop — which
                # would orphan the intervals started after it — and does not fail an otherwise-passing
                # scenario over evidence I/O. The gap is disclosed loudly (warning), never a phantom
                # artifact with no file behind it. Narrow on purpose: a genuine bug in a stop()/
                # transform (e.g. AttributeError) still surfaces rather than being swallowed here.
                _logger.warning("dropping %s evidence: capture stop failed: %s", interval.kind, exc)
                continue
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

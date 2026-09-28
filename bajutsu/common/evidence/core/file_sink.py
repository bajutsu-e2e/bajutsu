"""The sink that writes artifacts to disk under the run directory."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.common.drivers import base
from bajutsu.common.evidence import intervals
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.scenario import Redact

from ._functions import _interval_filename, begin_after_screenshot, capture, write_wait_diagnostic
from ._shared import _logger
from .artifact import Artifact

if TYPE_CHECKING:
    from bajutsu.common.orchestrator.waits import WaitTrace
    from bajutsu.common.platform_lifecycle import ReadinessResult


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
        on_video_start_stall: Callable[[], None] | None = None,
        video_extension: str = "mp4",
    ) -> None:
        self.udid = udid
        # The container the recording driver actually writes (`mp4` for simctl/adb, `webm` for
        # Playwright) — the caller derives it from the driver so the reserved filename matches the
        # real bytes (see `_interval_filename`).
        self.video_extension = video_extension
        self.log_predicate = log_predicate
        self.log_subsystem = log_subsystem  # for appTrace: the app's os_log subsystem
        self.redactor = Redactor(redact, values=secrets)
        # Every byte this sink writes goes through the run's artifact writer, which holds the run
        # directory so nothing here does (BE-0331).
        self._writer = RunArtifactWriter(run_dir, self.redactor)
        # When set (a web or Android lane), interval evidence comes from this driver-supplied provider
        # instead of the simctl starters below — the device pool injects the driver's `driver_interval`.
        self.driver_interval = driver_interval
        # Captures the environment already began before the app launched (Android's video, so
        # the cold-start frames are recorded); the sink adopts the running one at scenario start rather
        # than starting a fresh one on demand. Keyed by kind — at most one per kind.
        self._prestarted = {iv.kind: iv for iv in (prestarted_intervals or [])}
        # The launch readiness outcome and the run's BE-0049 provenance, folded into a first-wait
        # timeout diagnostic so the failure is decidable from artifacts alone (BE-0231 Unit 1).
        self.readiness = readiness
        self.provenance = provenance
        # Called when a recording that was asked to confirm its start never did (BE-0354). The device
        # pool wires it to a per-lease flag the crash retry reads to pick its recovery rung; None (a
        # sink built outside a lease) simply drops the signal. Purely advisory — the evidence gap
        # itself is already warned about where the confirmation timed out.
        self.on_video_start_stall = on_video_start_stall

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
        elements_source: str | None = None,
        reuse_before_screenshot: Artifact | None = None,
    ) -> list[Artifact]:
        return capture(
            driver,
            self._writer,
            step_id,
            kinds,
            elements=elements,
            elements_source=elements_source,
            reuse_before_screenshot=reuse_before_screenshot,
        )

    def begin_after_screenshot(
        self, driver: base.Driver, step_id: str
    ) -> Callable[[], list[Artifact]]:
        return begin_after_screenshot(driver, self._writer, step_id)

    def wait_diagnostic(
        self,
        step_id: str,
        *,
        trace: WaitTrace,
        elements: list[base.Element],
    ) -> Artifact | None:
        """Write the first-wait timeout diagnostic under <step_id>/ and record it as an artifact."""
        name = write_wait_diagnostic(
            self._writer,
            step_id,
            trace=trace,
            elements=elements,
            readiness=self.readiness,
            provenance=self.provenance,
        )
        return Artifact(name, "waitDiagnostic", "runner")

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        """Start the whole-scenario recordings under <scenario_id>/.

        A kind the environment already began before launch (Android's video, `_prestarted`)
        is adopted rather than started, so the recording spans the app launch; the finalized file is
        relocated here on stop. Otherwise a driver-supplied lane records via the injected
        `driver_interval` provider (Playwright-native on web, adb `logcat` on Android),
        and failing that the simctl starters drive iOS, which need a `udid`.
        """
        if not kinds:
            return []
        if not (self._prestarted or self.driver_interval is not None or self.udid is not None):
            return []  # no lane can record: skip without creating an empty scenario dir
        started: list[intervals.Interval] = []
        for token in kinds:
            kind = token.partition(".")[0]
            # An external recorder (simctl, screenrecord, Playwright) writes the file itself, so the
            # sink reserves the path; `finish_scenario_intervals` below closes the loop (BE-0331).
            target = self._writer.reserve(
                f"{scenario_id}/{_interval_filename(kind, self.video_extension)}"
            )
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
                interval = self._start_simctl_interval(kind, target, scenario_id)
                if interval is not None:
                    started.append(interval)
        # A recording that was asked to confirm its start and never did says the device's capture
        # pipeline is not producing — the earliest symptom of the wedge whose recovery rung the crash
        # retry picks from it (BE-0354). Reported once per scenario, after every kind has started.
        if self.on_video_start_stall is not None and any(
            iv.start_confirmed is False for iv in started
        ):
            self.on_video_start_stall()
        return started

    def _start_simctl_interval(
        self, kind: str, target: Path, scenario_id: str
    ) -> intervals.Interval | None:
        """Start one simctl interval capture (iOS), or None for a kind this lane does not record."""
        assert self.udid is not None
        if kind == "video":
            return intervals.start_video(self.udid, target, confirm_started=True)
        if kind == "deviceLog":
            return intervals.start_device_log(self.udid, target, self.log_predicate)
        if kind == "appTrace" and self.log_subsystem:
            return intervals.start_app_trace(
                self.udid,
                target,
                self._writer.reserve(f"{scenario_id}/appTrace.json"),
                self.log_subsystem,
            )
        return None

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        """Finalize each recording into an artifact.

        Artifact names are relative to the run dir so the HTML report (written there)
        can link/embed them directly. Each finalized file crosses redaction here — the loop
        `start_scenario_intervals`' reservation left open (BE-0331).
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
            # The recording landed in the scenario dir it was reserved under, so its artifact name is
            # that reservation's — a transform (appTrace's parse, adb's pull) only ever renames within it.
            name = f"{scenario_id}/{path.name}"
            # appTrace also has a raw stream beside it; both must be scrubbed before the artifact ships.
            # A video is opaque bytes the sink cannot inspect, so it is recorded as written unmasked
            # rather than scrubbed — the honesty BE-0151 established for screenshots. Keyed on
            # `interval.kind`, not the file's extension: a Playwright recording's real container
            # (webm) must be just as opaque to the scrubber as simctl/adb's mp4.
            to_scrub = [(name, interval.kind == "video")]
            if interval.kind == "appTrace":
                to_scrub.append((f"{scenario_id}/appTrace.raw", False))
            unsafe = [n for n, opaque in to_scrub if not self._scrub_or_record(n, opaque=opaque)]
            if unsafe:
                # Redaction is a security control: if we couldn't read a file to scrub it, don't ship
                # the artifact (fail closed), and name the offending file loudly rather than leak it.
                _logger.warning(
                    "dropping %s evidence: could not read %s to redact secrets (failing closed)",
                    interval.kind,
                    ", ".join(unsafe),
                )
                continue
            out.append(Artifact(name=name, kind=interval.kind, provider=interval.provider))
        return out

    def _scrub_or_record(self, name: str, *, opaque: bool) -> bool:
        """Close a reserved recording's redaction loop; return whether it is safe to ship."""
        if opaque:
            self._writer.record_unmasked(name)
            return True
        return self._writer.scrub_reserved(name)

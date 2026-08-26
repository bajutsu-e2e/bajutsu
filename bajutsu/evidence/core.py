"""Evidence capture: instant and interval artifacts written during a run.

Instant artifacts (screenshot / elements) are written after each step; interval
artifacts (video / deviceLog / appTrace) are recorded for the whole scenario.
Instant captures land in run_dir/<step_id>/; interval captures run for the whole
scenario and land in run_dir/<scenario_id>/. Every artifact records its provider so
the manifest shows where it came from.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol

from bajutsu.drivers import base
from bajutsu.evidence import intervals
from bajutsu.evidence.redaction import Redactor
from bajutsu.evidence.sink import RunArtifactWriter
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


def displayed_screenshot(screenshot_names: list[str]) -> str | None:
    """Which of a step's screenshots a viewer shows.

    The post-action `after.png` when the run recorded one, else the first screenshot it did record.
    Every consumer that shows a step's screenshot resolves it through here — the HTML report's steps
    table and element viewer, the serve editor's element picker, and the triage context handed to a
    failure investigator — so none of them disagrees about which screenshot a step "is". Preferring
    `after.png` keeps that image next to the tree in `elements.json`: the pre-step baseline writes
    the pre-action tree (BE-0341), but the file has one fixed name, so the always-on post-step
    `elements` capture replaces it with the post-action tree. The fallback covers the one path that
    returns before that post-step call — a step failing on `UncoveredSystemAlertLocale` — so a step
    still shows whichever screenshot it has.

    The preference is sound only because the post-step `elements` write is unconditional, which is
    true from this change onward and not of runs recorded before it. A stored run made under a
    narrowed `capture` list can hold a pre-action `elements.json` next to an `after.png`, and
    nothing in the manifest distinguishes the two — `kind` records the artifact, not which side of
    the action it was taken on. Reading such a run (a re-rendered report, the serve editor's picker,
    triage) therefore pairs its post-action image with a pre-action tree, where before it paired
    `before.png` with that same tree. Pairing them correctly would mean recording the capture token
    on each artifact entry, a manifest-schema change left to its own item.

    Args:
        screenshot_names: every `screenshot` artifact name the step recorded, in capture order.
            Names are re-rooted under the step id (`00-login/step0/after.png`), so the post-action
            one is matched on its filename — the only name `screenshot.after` writes.
    """
    return next(
        (n for n in screenshot_names if PurePosixPath(n).name == "after.png"),
        screenshot_names[0] if screenshot_names else None,
    )


def write_elements(
    driver: base.Driver,
    writer: RunArtifactWriter,
    prefix: str,
    *,
    elements: list[base.Element] | None = None,
) -> str:
    """Write the element tree to `<prefix>/elements.json`, returning the artifact name.

    Uses `elements` if given, otherwise queries the driver now. The sink masks each element's value
    structurally on the way in, so the redaction the tree needs is a property of where it lands
    rather than of this writer (BE-0331).
    """
    name = f"{prefix}/elements.json"
    writer.write_elements(name, elements if elements is not None else driver.query())
    return name


def write_screenshot(
    driver: base.Driver, writer: RunArtifactWriter, prefix: str, filename: str = "after.png"
) -> str:
    """Write a screenshot to `<prefix>/<filename>`, returning the artifact name.

    The driver writes the image itself, so the sink reserves the path and records that the bytes
    went in uninspected — pixels cannot be masked (BE-0151).
    """
    name = f"{prefix}/{filename}"
    driver.screenshot(str(writer.reserve(name)))
    writer.record_unmasked(name)
    return name


def write_raw_tree(driver: base.Driver, writer: RunArtifactWriter, prefix: str) -> list[str]:
    """Write the device's own reply behind the driver's last read (`rawTree` capture kind), if it has any.

    A no-op for any backend that does not implement `base.RawSourceProvider` (every backend but `adb`
    and XCUITest today) or has not read yet — so a scenario that requests `rawTree` on a backend without
    one simply gets nothing, the same degrade `ViewportProvider`/`ReadLagProvider` callers already make.
    Writes `hierarchy.raw<suffix>` (the device's/runner's own reply, untouched by any of bajutsu's
    processing — adb's `uiautomator dump`/resident XML before narrowing, XCUITest's undecoded
    `GET /elements` body; `<suffix>` is `base.RawSource.suffix`, the backend's own dump format) and, only
    when the backend applied a structural transform that changed it (adb's resident channel stripping
    SystemUI decor windows), `hierarchy.parsed-input<suffix>` — what the parser actually consumed after
    that transform — so a mismatch between a resolved coordinate and the real screen can be traced to the
    device's/runner's own reply versus bajutsu's processing of it (BE-0351). Returns the artifact names
    written, relative to the run dir.

    Also a no-op, loudly, when the run's redactor `has_label_rules`: `redact_elements` (behind
    `elements.json`) blanks a labeled element's `value` structurally, using the parsed tree it has and
    this function does not; `redact_text` over free text can only catch a key pattern or a literal
    secret value, so it would write an unmasked superset of what `elements.json` just masked. Refusing
    the artifact is the safe direction — a missing diagnostic file costs an investigation a round trip,
    an unmasked secret on disk does not un-happen.
    """
    if not isinstance(driver, base.RawSourceProvider):
        return []
    raw = driver.last_raw_source()
    if raw is None:
        return []
    if writer.redactor.has_label_rules:
        _logger.warning(
            "rawTree capture skipped: redact.labels masks an element's value structurally, which "
            "the raw dump's free-text redaction cannot honor — refusing rather than writing an "
            "unmasked superset of what elements.json just masked"
        )
        return []
    out: list[str] = []
    for filename, text in (
        (f"hierarchy.raw{raw.suffix}", raw.text),
        (f"hierarchy.parsed-input{raw.suffix}", raw.parsed_input),
    ):
        if text is None:
            continue
        name = f"{prefix}/{filename}"
        writer.write_text(name, text)
        out.append(name)
    return out


def write_wait_diagnostic(
    writer: RunArtifactWriter,
    prefix: str,
    *,
    trace: WaitTrace,
    elements: list[base.Element],
    readiness: ReadinessResult | None,
    provenance: Mapping[str, object] | None,
) -> str:
    """Write a `for`-wait timeout diagnostic (redacted tree + readiness + trace + provenance).

    Everything needed to decide *why* a first `wait` timed out, in one self-contained file so a
    rerun-to-green does not discard the evidence (BE-0231 Unit 1). `awaitedEverQueryable` is always
    false: a `for` wait returns the instant the element matches, so a timeout means it was never
    queryable across the recorded polls. Pure diagnosis — never a verdict input (prime directive 1).

    Returns:
        The artifact name, relative to the run dir.
    """
    # The tree is masked structurally here rather than by the sink's free-text pass, because it is
    # only one field of a larger document — the sink's element entry point takes a whole tree.
    els = writer.redactor.redact_elements(elements)
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
    name = f"{prefix}/wait-timeout.json"
    writer.write_json(name, doc)
    return name


def capture(
    driver: base.Driver,
    writer: RunArtifactWriter,
    prefix: str,
    kinds: list[str],
    *,
    elements: list[base.Element] | None = None,
) -> list[Artifact]:
    """Capture the requested instant kinds under `<prefix>/`; return their artifact records.

    Names are relative to the run dir (`00-slug/step0/after.png`), so the HTML report written there
    can reference them directly. An unmatched-only kind list writes nothing and creates no directory.
    """
    out: list[Artifact] = []
    # `rawTree` last, whatever order the scenario listed the kinds in: `write_elements` may issue the
    # read itself (`elements is None`, line 69 above), and `write_raw_tree` persists the driver's
    # *last* read — so a `[rawTree, elements]` order would pair a stale dump with a fresh
    # elements.json, exactly the mismatch this pair of artifacts exists to rule out. `sorted` is
    # stable, so no other kind's relative order moves.
    for token in sorted(kinds, key=lambda t: t.partition(".")[0] == "rawTree"):
        kind, _, modifier = token.partition(".")
        if kind == "rawTree":
            out.extend(
                Artifact(name, "rawTree", "driver")
                for name in write_raw_tree(driver, writer, prefix)
            )
        elif kind == "elements":
            out.append(
                Artifact(
                    write_elements(driver, writer, prefix, elements=elements), "elements", "driver"
                )
            )
        elif kind == "screenshot":
            out.append(
                Artifact(
                    write_screenshot(driver, writer, prefix, f"{modifier or 'after'}.png"),
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
        driver: base.Driver,  # noqa: ARG002  # EvidenceSink shape
        step_id: str,  # noqa: ARG002
        kinds: list[str],  # noqa: ARG002
        *,
        elements: list[base.Element] | None = None,  # noqa: ARG002
    ) -> list[Artifact]:
        return []

    def wait_diagnostic(
        self,
        step_id: str,  # noqa: ARG002  # EvidenceSink shape
        *,
        trace: WaitTrace,  # noqa: ARG002
        elements: list[base.Element],  # noqa: ARG002
    ) -> Artifact | None:
        return None

    def start_scenario_intervals(
        self,
        scenario_id: str,  # noqa: ARG002  # EvidenceSink shape
        kinds: list[str],  # noqa: ARG002
    ) -> list[intervals.Interval]:
        return []

    def finish_scenario_intervals(
        self,
        scenario_id: str,  # noqa: ARG002  # EvidenceSink shape
        started: list[intervals.Interval],  # noqa: ARG002
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
        on_video_start_stall: Callable[[], None] | None = None,
    ) -> None:
        self.udid = udid
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
    ) -> list[Artifact]:
        return capture(driver, self._writer, step_id, kinds, elements=elements)

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
            target = self._writer.reserve(f"{scenario_id}/{_interval_filename(kind)}")
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
            to_scrub = [name]
            if interval.kind == "appTrace":
                to_scrub.append(f"{scenario_id}/appTrace.raw")
            # A video is opaque bytes the sink cannot inspect, so it is recorded as written unmasked
            # rather than scrubbed — the honesty BE-0151 established for screenshots.
            unsafe = [n for n in to_scrub if not self._scrub_or_record(n)]
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

    def _scrub_or_record(self, name: str) -> bool:
        """Close a reserved recording's redaction loop; return whether it is safe to ship."""
        if PurePosixPath(name).suffix == ".mp4":
            self._writer.record_unmasked(name)
            return True
        return self._writer.scrub_reserved(name)

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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol

from bajutsu.common.drivers import base
from bajutsu.common.evidence import intervals
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.scenario import Redact

if TYPE_CHECKING:
    # Imported for typing only — importing at runtime would cycle (orchestrator imports this module).
    # The writer reads these by attribute, so it needs no runtime import.
    from bajutsu.common.orchestrator.waits import WaitTrace
    from bajutsu.common.platform_lifecycle import ReadinessResult

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
    # Which screen this file shows, as `"<driver>:<moment>"` — the driver whose read produced it,
    # and which side of the step's action it was taken on. Two artifacts describe the same screen
    # exactly when their `depicts` are equal, which is the entire contract: a consumer compares, and
    # never parses. `None` for a file that shows no screen (an interval recording, the wait
    # diagnostic) and for every run recorded before this field existed — where `step_view` falls
    # back to the pre-field choice rather than dropping frames a stored run has always shown.
    depicts: str | None = None


def _depicts(source: str, modifier: str) -> str:
    """The screen a capture token writes about: the reading driver and the side of the action."""
    return f"{source}:{modifier or 'after'}"


@dataclass(frozen=True)
class StepView:
    """The one screenshot and one element tree a viewer shows for a step.

    `paired` says whether the two describe the same screen. A viewer that draws element frames onto
    the image — the HTML report's element viewer, the serve editor's picker — must draw none when
    `paired` is false: the frames would land at coordinates that never described that image.
    """

    screenshot: str | None
    elements: str | None
    paired: bool


def _preferred_screenshot(names: list[str]) -> str | None:
    """The post-action `after.png` when the step recorded one, else the first it did record.

    The choice every consumer made before artifacts recorded what they depict, kept as the fallback
    for the two cases `depicts` cannot decide: a run recorded before the field existed, and a step
    none of whose screenshots matches its tree. Names are re-rooted under the step id
    (`00-login/step0/after.png`), so the post-action one is matched on its filename — the only name
    `screenshot.after` writes.
    """
    return next(
        (n for n in names if PurePosixPath(n).name == "after.png"),
        names[0] if names else None,
    )


def step_view(
    entries: Iterable[tuple[str, str, str | None]],
    *,
    exists: Callable[[str], bool] | None = None,
) -> StepView:
    """Resolve a step's artifacts to the one screenshot and one tree a viewer shows.

    Every consumer that shows a step's evidence resolves it through here — the HTML report's steps
    table and element viewer, the serve editor's element picker, and the triage context handed to a
    failure investigator — so none of them disagrees about which screen a step "is", nor about
    whether the image and the tree it shows describe that same screen.

    The tree is the **last** `elements` entry, not the first: `elements.json` has one fixed name, so
    a step's later write replaces its earlier one and only the last entry's `depicts` describes what
    the file now holds. The screenshot is the candidate whose `depicts` equals the tree's. With no
    such candidate the result carries `_preferred_screenshot`'s choice and `paired=False`, so a
    caller keeps an image to show and knows not to draw frames on it — the two cases being a `web`
    block (a native image beside a WebView tree, in the WebView's own coordinate space) and a run
    whose `after.png` the store no longer holds, leaving `before.png` beside a post-action tree.

    `paired` is false only when there is an image the tree does not describe, so a consumer can read
    it directly: a step whose screenshots the store no longer holds resolves to no image and reports
    as paired, since there is nothing there to mispair.

    A tree entry carrying no `depicts` is a run recorded before the field existed. Nothing in such a
    manifest distinguishes a pre-action tree from a post-action one, so the result reproduces the
    pre-field choice and reports it as paired: a stored run keeps the frames it has always drawn
    rather than losing them to a fact that was never written down.

    Args:
        entries: the step's artifacts as `(kind, name, depicts)`, in capture order.
        exists: whether the store actually holds a named artifact, or None to trust the manifest.
            Probed lazily, in preference order, until one candidate passes: the manifest can name a
            screenshot the store no longer holds (a run restored from Trash, or one synced into an
            object store that never received the last write), and this call site is a live
            object-store lookup on the hosted backend, so filtering every candidate up front would
            cost a round trip per recorded screenshot per step.
    """
    shots: list[tuple[str, str | None]] = []
    tree: tuple[str, str | None] | None = None
    for kind, name, depicts in entries:
        if kind == "screenshot":
            shots.append((name, depicts))
        elif kind == "elements":
            tree = (name, depicts)
    tree_name, tree_depicts = tree if tree is not None else (None, None)
    held = exists if exists is not None else (lambda _name: True)
    matching = [n for n, d in shots if tree_depicts is not None and d == tree_depicts]
    for name in matching:
        if held(name):
            return StepView(name, tree_name, True)
    rest = [n for n, _d in shots if n not in matching]
    fallback = _preferred_screenshot(rest)
    while fallback is not None and not held(fallback):
        rest = [n for n in rest if n != fallback]
        fallback = _preferred_screenshot(rest)
    # `paired` is false only when there is an image the tree does not describe. A step left with no
    # screenshot — none recorded, or none the store still holds — has nothing to mispair, so it
    # reports as paired: a viewer with no image draws no frames either way, and "these describe
    # different screens" would state a reason that is not the reason its frames are absent.
    return StepView(fallback, tree_name, fallback is None or tree_depicts is None)


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
                # `false` says the gate returned on the signal while the screen was still moving,
                # which is when a synthesized touch is dropped — the reading that separates "this
                # wait's element never came" from "the actuation before it never landed".
                "settled": readiness.settled,
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
    elements_source: str | None = None,
) -> list[Artifact]:
    """Capture the requested instant kinds under `<prefix>/`; return their artifact records.

    Names are relative to the run dir (`00-slug/step0/after.png`), so the HTML report written there
    can reference them directly. An unmatched-only kind list writes nothing and creates no directory.

    The `elements_source` argument names the driver `elements` were read from, when that is not the
    driver this call captures against. Inside a `web` block the two differ — a `WebContextDriver`
    cannot screenshot, so the pixels come from the native driver while the tree comes from the
    WebView — and recording each artifact's own source is what lets `step_view` refuse to pair them.
    It defaults to the capture driver's own name.
    """
    out: list[Artifact] = []
    tree_source = elements_source or driver.name
    # `rawTree` last, whatever order the scenario listed the kinds in: `write_elements` may issue the
    # read itself (the `elements is None` path in `write_elements`), and `write_raw_tree` persists
    # the driver's *last* read — so a `[rawTree, elements]` order would pair a stale dump with a fresh
    # elements.json, exactly the mismatch this pair of artifacts exists to rule out. `sorted` is
    # stable, so no other kind's relative order moves.
    for token in sorted(kinds, key=lambda t: t.partition(".")[0] == "rawTree"):
        kind, _, modifier = token.partition(".")
        if kind == "rawTree":
            out.extend(
                # `driver.name`, not `tree_source`: `write_raw_tree` reads `driver`'s own last
                # reply, so that is the screen this dump depicts even when the tree beside it came
                # from somewhere else. The two are identical on every path today — the run loop
                # drops `rawTree` inside a `web` block — and stating the real source here is what
                # keeps that true if a later `rawTree.before` lands on the pre-step baseline, where
                # the elements source can be a different driver.
                Artifact(name, "rawTree", "driver", _depicts(driver.name, modifier))
                for name in write_raw_tree(driver, writer, prefix)
            )
        elif kind == "elements":
            out.append(
                Artifact(
                    write_elements(driver, writer, prefix, elements=elements),
                    "elements",
                    "driver",
                    _depicts(tree_source, modifier),
                )
            )
        elif kind == "screenshot":
            out.append(
                Artifact(
                    write_screenshot(driver, writer, prefix, f"{modifier or 'after'}.png"),
                    "screenshot",
                    "driver",
                    _depicts(driver.name, modifier),
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
        elements_source: str | None = None,
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
        elements_source: str | None = None,  # noqa: ARG002
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
        elements_source: str | None = None,
    ) -> list[Artifact]:
        return capture(
            driver,
            self._writer,
            step_id,
            kinds,
            elements=elements,
            elements_source=elements_source,
        )

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

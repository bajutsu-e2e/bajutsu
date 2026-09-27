"""Name each artifact and write it through the run's sink."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from bajutsu.common.drivers import base
from bajutsu.common.evidence.sink import RunArtifactWriter

from ._shared import _logger
from .artifact import Artifact
from .deferred_screenshot_sink import DeferredScreenshotSink
from .evidence_sink import EvidenceSink
from .step_view import StepView

if TYPE_CHECKING:
    # Imported for typing only — importing at runtime would cycle (orchestrator imports this module).
    # The writer reads these by attribute, so it needs no runtime import.
    from bajutsu.common.orchestrator.waits import WaitTrace
    from bajutsu.common.platform_lifecycle import ReadinessResult

# scenario-dir file names for interval kinds — one source of truth for both the simctl (iOS)
# and the Playwright (web) providers, so the two never drift. `video`'s extension varies by
# provider (see `_interval_filename`): simctl/adb record real ISO base media (mp4), Playwright
# records Matroska/WebM, and naming the latter `.mp4` served it with a lying Content-Type.
_INTERVAL_FILE = {"deviceLog": "device.log", "appTrace": "appTrace.raw"}


def _interval_filename(kind: str, video_extension: str = "mp4") -> str:
    """The artifact filename for an interval `kind`.

    `video_extension` names the container the recording driver actually produces (`FileSink`'s
    caller derives it from the driver, e.g. `"webm"` for Playwright) — the filename must match the
    real bytes so a served Content-Type (extension-derived, `content_type_for`) and the report's
    `<video>` element both describe the file honestly rather than assuming every backend's video is
    ISO base media.
    """
    if kind == "video":
        return f"scenario.{video_extension}"
    return _INTERVAL_FILE.get(kind, kind)


def _depicts(source: str, modifier: str) -> str:
    """The screen a capture token writes about: the reading driver and the side of the action."""
    return f"{source}:{modifier or 'after'}"


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


def begin_after_screenshot(
    driver: base.Driver, writer: RunArtifactWriter, prefix: str
) -> Callable[[], list[Artifact]]:
    """Start the step's mandatory `after.png` now; the returned join finishes it (BE-0407 Unit 2).

    The shot is a device round trip, so the run loop starts it right after the action and joins it at
    the end of the same step, letting the post-step tree read run inside it. Whether it may actually
    overlap is the backend's own answer, decided here (`start_after_screenshot` below decides the
    sink's half): a backend that does not implement `base.BackgroundScreenshotProvider` is shot
    synchronously, and its join only hands back the record, so nothing about its timing moves.

    The pending shot never outlives its own step, which is what keeps this cheap: there is no capture
    to cancel at a scenario boundary and nothing to await before the report is written, and a failure
    surfaces out of the join with the same exception the synchronous shot would have raised, inside
    the step that took it.

    Returns:
        The join, which completes the capture and returns the step's screenshot artifact record.
    """

    def record(name: str) -> list[Artifact]:
        return [Artifact(name, "screenshot", "driver", _depicts(driver.name, "after"))]

    # `write_screenshot`, not an inlined copy of it, so a later change to how a screenshot artifact is
    # produced reaches `after.png` and `before.png` alike. Only the deferred branch below has to
    # reserve the path itself, because it hands that path to the driver before there is anything to
    # record.
    if not isinstance(driver, base.BackgroundScreenshotProvider):
        synchronous = record(write_screenshot(driver, writer, prefix))
        return lambda: synchronous
    name = f"{prefix}/after.png"
    # Restricted up front, not only at the join: a screenshot can hold on-screen secrets, and the
    # ordinary reserve/record pair would leave this one at the ambient umask for the whole overlap
    # window rather than for no time at all (BE-0131).
    reserved = writer.reserve_restricted(name)
    wait = driver.screenshot_in_background(str(reserved))

    def join() -> list[Artifact]:
        try:
            wait()
        except Exception:
            # The reservation pre-created the file, so a shot that never wrote would leave an empty
            # `after.png` behind. No manifest names it — the artifact record below is never
            # produced — but the run directory is shipped whole to an object store, so it would
            # reach an investigator as a zero-byte screenshot of the very failure they opened the
            # run for.
            reserved.unlink(missing_ok=True)
            raise
        # Deferred with the bytes, not taken with the reservation: `record_unmasked` restricts the
        # file the recorder wrote (BE-0131), so it has to run once that file actually exists.
        writer.record_unmasked(name)
        return record(name)

    return join


def reuse_screenshot(
    writer: RunArtifactWriter, source_name: str, prefix: str, filename: str = "before.png"
) -> str:
    """Write `<prefix>/<filename>` by copying an already-written screenshot's bytes.

    Valid only when the screen has not changed since `source_name` was captured (BE-0407 Unit 1) —
    a stronger claim than "nothing *bajutsu* has actuated since": the caller is the one place that
    knows whether an asynchronous interstitial could have arrived in between, the same distinction
    `loop.py`'s `prev_after` tree reuse already draws. When it holds, the previous step's `after.png`
    and this step's `before.png` describe the identical screen, so a local copy stands in for a
    fresh `driver.screenshot()` call.
    """
    name = f"{prefix}/{filename}"
    writer.copy_bytes(source_name, name)
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
    reuse_before_screenshot: Artifact | None = None,
) -> list[Artifact]:
    """Capture the requested instant kinds under `<prefix>/`; return their artifact records.

    Names are relative to the run dir (`00-slug/step0/after.png`), so the HTML report written there
    can reference them directly. An unmatched-only kind list writes nothing and creates no directory.

    The `elements_source` argument names the driver `elements` were read from, when that is not the
    driver this call captures against. Inside a `web` block the two differ — a `WebContextDriver`
    cannot screenshot, so the pixels come from the native driver while the tree comes from the
    WebView — and recording each artifact's own source is what lets `step_view` refuse to pair them.
    It defaults to the capture driver's own name.

    `reuse_before_screenshot`, when its own `kind` is `"screenshot"`, names an already-written
    artifact whose bytes describe the identical screen a `screenshot.before` token would otherwise
    capture fresh (BE-0407 Unit 1) — ordinarily the previous step's `after.png`. The caller is the
    one place that knows whether the screen has genuinely not changed since (an asynchronous
    interstitial is not ruled out just because nothing *bajutsu* has actuated); pass `None` wherever
    that is not established. Ignored by every token but `screenshot.before`, and falls back to a
    fresh capture on any other `kind` or when the artifact's own bytes are no longer on disk.
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
            filename = f"{modifier or 'after'}.png"
            name: str | None = None
            if (
                modifier == "before"
                and reuse_before_screenshot is not None
                and reuse_before_screenshot.kind == "screenshot"
            ):
                try:
                    name = reuse_screenshot(writer, reuse_before_screenshot.name, prefix, filename)
                except OSError as exc:
                    # `reuse_before_screenshot` names a file this same writer wrote moments ago
                    # (BE-0407 Unit 1), not a historical manifest entry — unlike `step_view`'s own
                    # `exists` check, this is not an expected gap, so it is worth a loud warning even
                    # though the fallback below still gets the step a `before.png`.
                    _logger.warning(
                        "screenshot reuse failed for %s (source %s), falling back to a fresh "
                        "capture: %s",
                        f"{prefix}/{filename}",
                        reuse_before_screenshot.name,
                        exc,
                    )
            if name is None:
                name = write_screenshot(driver, writer, prefix, filename)
            out.append(Artifact(name, "screenshot", "driver", _depicts(driver.name, modifier)))
        # actionLog lives in the manifest; video / deviceLog / appTrace are intervals.
    return out


def start_after_screenshot(
    sink: EvidenceSink, driver: base.Driver, step_id: str
) -> Callable[[], list[Artifact]]:
    """Begin the step's mandatory `after.png`; the returned join yields its artifact records.

    Deferred — overlapping the shot with whatever the caller does before joining — only when the sink
    can hand back a join *and* the backend says a second call may be in flight on its channel. This
    decides the first half; `begin_after_screenshot` decides the second. A sink with no join takes the
    ordinary capture call it already serves, which is the same shot at the same moment it was taken
    before this seam existed (BE-0407 Unit 2).
    """
    if isinstance(sink, DeferredScreenshotSink):
        return sink.begin_after_screenshot(driver, step_id)
    shot = sink.capture(driver, step_id, ["screenshot.after"])
    return lambda: shot

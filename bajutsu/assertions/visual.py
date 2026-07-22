"""Visual-assertion image preprocessing.

The coordinate math, cropping, masking, and Pillow file I/O a `visual` assertion needs before it
hands off to `bajutsu.evidence.visual`'s pixel-compare engine. Frames are in element points; the screenshot
is in device pixels, so everything here resolves selectors and scales frames into pixel space.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from bajutsu.assertions._common import AssertionResult, _resolve_one, sel_str
from bajutsu.drivers import base
from bajutsu.scenario import (
    ExcludeRegion,
    Selector,
    SelectorRegion,
    VisualMatch,
)


@dataclass(frozen=True)
class VisualEvidence:
    """Image evidence for a visual assertion, carried into the manifest/report.

    Paths are *run-dir-relative* (the same scheme as artifacts), so the self-contained
    report and the serve UI can reference them. `baseline_name` is the YAML key into the
    baselines dir — what `approve` promotes the actual screenshot to.
    """

    baseline_name: str
    actual: str  # the captured screenshot
    baseline: str | None = None  # the baseline copy in the run dir (None if missing)
    diff: str | None = None  # the diff visualization (None when identical / missing)
    diff_pct: float | None = None
    missing: bool = False  # baseline did not exist yet (first run)
    engine: str | None = None  # the compare engine used (exact / pixelmatch; BE-0165)
    # Provenance for element-scoped comparison / selector masking (BE-0171).
    element_scoped: bool = False  # the comparison was cropped to one element's frame
    # selectors that resolved to a mask, in order (a list so it round-trips through the manifest)
    masked_selectors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VisualContext:
    """Paths a visual assertion needs.

    The current screenshot, the baselines directory, where to write diff images, and the run dir
    root (so image paths can be expressed run-dir-relative for the report).
    """

    screenshot_path: Path
    baselines_dir: Path
    diff_dir: Path
    run_dir: Path
    default_compare: str = "exact"


def _visual_scale(
    screenshot_path: Path, elements: list[base.Element]
) -> tuple[float, float] | None:
    """The screenshot-pixel per element-point scale, or None if it can't be derived.

    Element frames are in points; the screenshot is in device pixels (2x/3x on retina). The scale
    is the screenshot's pixel size over the point-space screen size (the element extent), so a
    resolved frame maps onto the actual image. Returns None when there are no elements to size the
    screen from — the caller then can't resolve any selector to a frame.
    """
    from bajutsu.elements import screen_size_from_elements

    sw, sh = screen_size_from_elements(elements)
    if sw <= 0 or sh <= 0:
        return None
    from PIL import Image

    with Image.open(screenshot_path) as img:
        iw, ih = img.size
    return iw / sw, ih / sh


def _frame_to_px(frame: base.Frame, scale: tuple[float, float]) -> ExcludeRegion:
    """A point-space element frame scaled to a screenshot-pixel rectangle."""
    sx, sy = scale
    x, y, w, h = frame
    return ExcludeRegion(x=round(x * sx), y=round(y * sy), w=round(w * sx), h=round(h * sy))


def _resolve_mask(elements: list[base.Element], sel: Selector) -> tuple[base.Element | None, str]:
    """Resolve a selector *mask*: not-found is a no-op, ambiguous fails (prime directive 2).

    Returns (element, "") when a single element matches, (None, "") when nothing matches (there is
    nothing on screen to hide), and (None, reason) when the selector is ambiguous.
    """
    try:
        return base.resolve_unique(elements, sel.as_selector()), ""
    except base.ElementNotFound:
        return None, ""
    except base.AmbiguousSelector as e:
        return None, str(e)


def _shift(region: ExcludeRegion, dx: float, dy: float) -> ExcludeRegion:
    """A mask rectangle translated into a cropped image's local coordinates."""
    return ExcludeRegion(x=region.x - dx, y=region.y - dy, w=region.w, h=region.h)


@dataclass(frozen=True)
class _Prepared:
    """The result of visual preprocessing: what to compare, plus the frame data later steps reuse.

    `compare_actual` is the image handed to the compare engine (the element crop when scoped, else
    the whole screenshot); `actual_rel` is its run-dir-relative path for the evidence. `crop` and
    `scale` are None for a whole-screen comparison and set once frames were resolved.
    """

    compare_actual: Path
    actual_rel: str
    crop: ExcludeRegion | None
    scale: tuple[float, float] | None


def _prepare_visual_comparison(
    ctx: VisualContext, a: VisualMatch, elements: list[base.Element], name: str
) -> _Prepared | AssertionResult:
    """Resolve frames and crop the actual to the scoped element, before the baseline check.

    Element scoping and selector masks (BE-0171) resolve against the live element tree in
    screenshot-pixel space; a comparison that needs neither keeps the whole-screen behavior. The
    crop happens *before* the missing-baseline check because it is both what we compare and what
    `approve` promotes — so the baseline is the element even on the first run (otherwise the first
    approve would store a whole-screen baseline and every later run would size-mismatch).

    Returns the prepared comparison, or an AssertionResult when preprocessing fails (Pillow missing,
    no elements to resolve against, or an unresolvable / empty-frame element scope).
    """
    detail = f"visual ≈ {a.baseline}"
    actual_rel = _rel(ctx.run_dir, ctx.screenshot_path)
    needs_frames = a.element is not None or any(
        isinstance(r, SelectorRegion) for r in a.exclude or []
    )
    if not needs_frames:
        return _Prepared(ctx.screenshot_path, actual_rel, crop=None, scale=None)

    try:
        from PIL import Image
    except ImportError:
        return AssertionResult(
            False, "visual", detail, "visual assertions need the 'visual' extra (Pillow)"
        )
    scale = _visual_scale(ctx.screenshot_path, elements)
    if scale is None:
        return AssertionResult(
            False, "visual", detail, "cannot resolve selectors: no elements on screen"
        )
    if a.element is None:
        return _Prepared(ctx.screenshot_path, actual_rel, crop=None, scale=scale)

    el, err = _resolve_one(elements, a.element)
    if el is None:
        ev = VisualEvidence(baseline_name=a.baseline, actual=actual_rel, element_scoped=True)
        return AssertionResult(False, "visual", detail, f"element {err}", visual=ev)
    crop = _frame_to_px(el["frame"], scale)
    if crop.w <= 0 or crop.h <= 0:
        # A zero-area frame (an off-screen / collapsed element) can't be cropped — fail
        # cleanly rather than letting Pillow raise on an empty image.
        ev = VisualEvidence(baseline_name=a.baseline, actual=actual_rel, element_scoped=True)
        return AssertionResult(
            False,
            "visual",
            detail,
            f"element has an empty frame: {sel_str(a.element)}",
            visual=ev,
        )
    ctx.diff_dir.mkdir(parents=True, exist_ok=True)
    cropped_path = ctx.diff_dir / f"actual-{name}"
    box = (int(crop.x), int(crop.y), int(crop.x + crop.w), int(crop.y + crop.h))
    with Image.open(ctx.screenshot_path) as img:
        img.crop(box).save(cropped_path)
    return _Prepared(cropped_path, _rel(ctx.run_dir, cropped_path), crop=crop, scale=scale)


def _resolve_masks(
    a: VisualMatch,
    elements: list[base.Element],
    scale: tuple[float, float] | None,
    crop: ExcludeRegion | None,
    detail: str,
) -> tuple[list[ExcludeRegion], list[str]] | AssertionResult:
    """Resolve the compare-time exclude masks, translating them into crop-local coordinates.

    Plain rectangles pass through unchanged; selector masks resolve against the live tree to a pixel
    rectangle (an ambiguous selector fails, a match of nothing is a no-op). When element-scoped, the
    masks are shifted into the crop's local coordinate space. Returns `(masks, masked_selectors)`, or
    an AssertionResult when an exclude selector is ambiguous.
    """
    masks: list[ExcludeRegion] = []
    masked_selectors: list[str] = []
    for r in a.exclude or []:
        if not isinstance(r, SelectorRegion):
            masks.append(r)
            continue
        assert scale is not None  # a SelectorRegion sets needs_frames, so scale is resolved
        el, err = _resolve_mask(elements, r.selector)
        if err:
            return AssertionResult(False, "visual", detail, f"exclude selector {err}")
        if el is None:
            continue  # matched nothing — nothing on screen to hide
        masks.append(_frame_to_px(el["frame"], scale))
        masked_selectors.append(sel_str(r.selector))
    if crop is not None:
        masks = [_shift(m, crop.x, crop.y) for m in masks]
    return masks, masked_selectors


def _resolve_baselines(ctx: VisualContext, baseline_path: Path, name: str) -> tuple[Path, Path]:
    """Prepare the run-dir baseline copy and the diff path for a compare.

    Copies the baseline into the run dir (so the report and serve are self-contained) and returns
    `(baseline_copy, diff_path)`. Called only once the baseline is known to exist.
    """
    ctx.diff_dir.mkdir(parents=True, exist_ok=True)
    diff_path = ctx.diff_dir / f"diff-{name}"
    baseline_copy = ctx.diff_dir / f"baseline-{name}"
    shutil.copyfile(baseline_path, baseline_copy)
    return baseline_copy, diff_path


def _eval_visual(
    ctx: VisualContext | None, a: VisualMatch, elements: list[base.Element]
) -> AssertionResult:
    detail = f"visual ≈ {a.baseline}"
    if ctx is None:
        return AssertionResult(False, "visual", detail, "no visual context provided")
    baseline_path = ctx.baselines_dir / a.baseline
    # Flatten any path separators in the baseline key for the in-run copy/diff filenames.
    name = Path(a.baseline).name

    # 1. Preprocess: resolve frames and crop the actual to the scoped element.
    prepared = _prepare_visual_comparison(ctx, a, elements, name)
    if isinstance(prepared, AssertionResult):
        return prepared

    # 2. Baseline: first run (or a brand-new screen) has nothing to compare against. Report the
    # actual (the element crop, when scoped) so it can be reviewed and approved into a baseline.
    if not baseline_path.is_file():
        ev = VisualEvidence(
            baseline_name=a.baseline,
            actual=prepared.actual_rel,
            missing=True,
            element_scoped=prepared.crop is not None,
        )
        return AssertionResult(
            False, "visual", detail, f"baseline not found: {a.baseline}", visual=ev
        )

    try:
        from bajutsu.evidence.visual import compare_images
    except ImportError:
        return AssertionResult(
            False, "visual", detail, "visual assertions need the 'visual' extra (Pillow)"
        )

    engine = a.compare or ctx.default_compare
    if engine == "exact" and {"color_tolerance", "antialiasing"} & a.model_fields_set:
        return AssertionResult(
            False,
            "visual",
            detail,
            "colorTolerance/antialiasing are set but the resolved engine is 'exact' "
            "(set compare: pixelmatch or the target's visualCompare)",
        )

    masks_or_result = _resolve_masks(a, elements, prepared.scale, prepared.crop, detail)
    if isinstance(masks_or_result, AssertionResult):
        return masks_or_result
    masks, masked_selectors = masks_or_result

    # 3. Compare: copy the baseline into the run dir, prepare the diff path, run the engine.
    baseline_copy, diff_path = _resolve_baselines(ctx, baseline_path, name)
    result = compare_images(
        prepared.compare_actual,
        baseline_path,
        engine=engine,
        threshold=a.threshold,
        color_tolerance=a.color_tolerance,
        antialiasing=a.antialiasing,
        exclude=masks or None,
        diff_path=diff_path,
    )

    # 4. Build the result and its evidence.
    ev = VisualEvidence(
        baseline_name=a.baseline,
        actual=prepared.actual_rel,
        baseline=_rel(ctx.run_dir, baseline_copy),
        diff=_rel(ctx.run_dir, diff_path) if (not result.ok and diff_path.is_file()) else None,
        diff_pct=result.diff_pct,
        engine=engine,
        element_scoped=prepared.crop is not None,
        masked_selectors=masked_selectors,
    )
    return AssertionResult(result.ok, "visual", detail, result.reason, visual=ev)


def _rel(run_dir: Path, p: Path) -> str:
    """A run-dir-relative POSIX path for the report; falls back to the name if unrelated."""
    try:
        return p.relative_to(run_dir).as_posix()
    except ValueError:
        return p.name

"""Visual regression — pixel-level image comparison for deterministic assertions.

Compares a captured screenshot against a stored baseline image.  Differences are
reported as a percentage of changed pixels; an optional threshold allows minor
rendering variance.  Exclude regions (e.g. the status bar or clock) are masked
before comparison so dynamic content does not cause false failures.

The comparison engine is selectable (BE-0165): ``exact`` (pixel-perfect, the default)
or ``pixelmatch`` (perceptual YIQ color distance with anti-aliasing detection).

Requires ``Pillow`` (``pip install bajutsu[visual]``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from bajutsu.scenario import ExcludeRegion

_Y_R, _Y_G, _Y_B = 0.29889531, 0.58662247, 0.11448223
_I_R, _I_G, _I_B = 0.59597799, -0.27417610, -0.32180189
_Q_R, _Q_G, _Q_B = 0.21147017, -0.52261711, 0.31114694

_MAX_YIQ_DELTA = 35215.0


@dataclass(frozen=True)
class CompareResult:
    """Outcome of comparing two screenshots: pass/fail, how much differed, and why."""

    ok: bool
    diff_pct: float  # percentage of pixels that differ (0.0-100.0)
    reason: str = ""


def compare_images(
    actual_path: Path,
    baseline_path: Path,
    *,
    engine: str = "exact",
    threshold: float = 0.0,
    color_tolerance: float = 0.1,
    antialiasing: bool = True,
    exclude: list[ExcludeRegion] | None = None,
    diff_path: Path | None = None,
) -> CompareResult:
    """Compare *actual_path* against *baseline_path* and return the result.

    Args:
        actual_path: Path to the captured screenshot.
        baseline_path: Path to the stored baseline image.
        engine: Comparison algorithm — ``"exact"`` or ``"pixelmatch"``.
        threshold: Maximum allowed diff percentage (0.0 = exact match).
        color_tolerance: Per-pixel perceptual tolerance for ``pixelmatch`` (0-1).
        antialiasing: Discount anti-aliased pixels from the diff (``pixelmatch``).
        exclude: Regions zeroed out in both images before comparison.
        diff_path: When given and images differ, a diff visualization is written here.
    """
    actual = Image.open(actual_path).convert("RGBA")
    baseline = Image.open(baseline_path).convert("RGBA")

    if actual.size != baseline.size:
        return CompareResult(
            ok=False,
            diff_pct=100.0,
            reason=f"size mismatch: actual {actual.size} vs baseline {baseline.size}",
        )

    if exclude:
        for r in exclude:
            box = (int(r.x), int(r.y), int(r.x + r.w), int(r.y + r.h))
            mask_fill = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
            actual.paste(mask_fill, box)
            baseline.paste(mask_fill, box)

    buf_a = actual.tobytes()
    buf_b = baseline.tobytes()

    if buf_a == buf_b:
        return CompareResult(ok=True, diff_pct=0.0)

    if engine == "pixelmatch":
        return _compare_pixelmatch(
            actual,
            baseline,
            buf_a,
            buf_b,
            threshold=threshold,
            color_tolerance=color_tolerance,
            antialiasing=antialiasing,
            diff_path=diff_path,
        )
    if engine == "exact":
        return _compare_exact(actual, baseline, threshold=threshold, diff_path=diff_path)
    raise ValueError(f"unknown visual compare engine {engine!r}: use 'exact' or 'pixelmatch'")


def _verdict(diff_count: int, total: int, threshold: float) -> CompareResult:
    """Build the pass/fail result from a diff pixel count."""
    if diff_count == 0:
        return CompareResult(ok=True, diff_pct=0.0)
    diff_pct = (diff_count / total) * 100.0
    ok = diff_pct <= threshold
    reason = "" if ok else f"visual diff {diff_pct:.2f}% exceeds threshold {threshold:.2f}%"
    return CompareResult(ok=ok, diff_pct=diff_pct, reason=reason)


def _compare_exact(
    actual: Image.Image,
    baseline: Image.Image,
    *,
    threshold: float,
    diff_path: Path | None,
) -> CompareResult:
    diff = ImageChops.difference(actual, baseline)
    total_pixels = actual.size[0] * actual.size[1]
    diff_bw = diff.convert("L")
    diff_count = sum(1 for px in diff_bw.tobytes() if px > 0)

    if diff_count > 0 and diff_path is not None:
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff.save(diff_path)

    return _verdict(diff_count, total_pixels, threshold)


def _compare_pixelmatch(
    actual: Image.Image,
    baseline: Image.Image,
    buf_a: bytes,
    buf_b: bytes,
    *,
    threshold: float,
    color_tolerance: float,
    antialiasing: bool,
    diff_path: Path | None,
) -> CompareResult:
    w, h = actual.size
    total = w * h
    max_delta = _MAX_YIQ_DELTA * color_tolerance * color_tolerance
    diff_pixels: list[int] = []

    for i in range(total):
        off = i * 4
        r1, g1, b1, a1 = buf_a[off], buf_a[off + 1], buf_a[off + 2], buf_a[off + 3]
        r2, g2, b2, a2 = buf_b[off], buf_b[off + 1], buf_b[off + 2], buf_b[off + 3]

        if r1 == r2 and g1 == g2 and b1 == b2 and a1 == a2:
            continue

        delta = _color_delta_sq(r1, g1, b1, a1, r2, g2, b2, a2)
        if delta <= max_delta:
            continue

        if antialiasing and (
            _is_antialiased(buf_a, i, w, h, buf_b, max_delta)
            or _is_antialiased(buf_b, i, w, h, buf_a, max_delta)
        ):
            continue

        diff_pixels.append(i)

    if diff_pixels and diff_path is not None:
        _write_pixelmatch_diff(baseline, diff_pixels, w, h, diff_path)

    return _verdict(len(diff_pixels), total, threshold)


def _color_delta_sq(
    r1: int, g1: int, b1: int, a1: int, r2: int, g2: int, b2: int, a2: int
) -> float:
    """YIQ perceptual color distance squared, alpha-blended over white."""
    dr: float
    dg: float
    db: float
    if a1 == a2 == 255:
        dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    else:
        ar1 = a1 / 255.0
        ar2 = a2 / 255.0
        dr = (r1 * ar1 + 255 * (1 - ar1)) - (r2 * ar2 + 255 * (1 - ar2))
        dg = (g1 * ar1 + 255 * (1 - ar1)) - (g2 * ar2 + 255 * (1 - ar2))
        db = (b1 * ar1 + 255 * (1 - ar1)) - (b2 * ar2 + 255 * (1 - ar2))
    dy = dr * _Y_R + dg * _Y_G + db * _Y_B
    di = dr * _I_R + dg * _I_G + db * _I_B
    dq = dr * _Q_R + dg * _Q_G + db * _Q_B
    return 0.5053 * dy * dy + 0.299 * di * di + 0.1957 * dq * dq


def _luminance(r: int, g: int, b: int) -> float:
    return r * _Y_R + g * _Y_G + b * _Y_B


def _is_antialiased(
    buf: bytes, pixel_idx: int, w: int, h: int, other_buf: bytes, max_delta: float
) -> bool:
    """Whether the pixel at *pixel_idx* in *buf* is likely anti-aliased.

    A pixel is anti-aliased if it sits on a high-contrast luminance edge in *buf*
    and the corresponding pixel in *other_buf* also has a neighbor that is close in
    color to the candidate.
    """
    x0 = pixel_idx % w
    y0 = pixel_idx // w
    off0 = pixel_idx * 4
    lum0 = _luminance(buf[off0], buf[off0 + 1], buf[off0 + 2])

    min_d = 0.0
    max_d = 0.0

    for dy in (-1, 0, 1):
        ny = y0 + dy
        if ny < 0 or ny >= h:
            continue
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx = x0 + dx
            if nx < 0 or nx >= w:
                continue
            off_n = (ny * w + nx) * 4
            lum_n = _luminance(buf[off_n], buf[off_n + 1], buf[off_n + 2])
            d = lum_n - lum0
            if d < min_d:
                min_d = d
            if d > max_d:
                max_d = d

    if max_d - min_d < 40 or not (abs(min_d) > 25 or abs(max_d) > 25):
        return False

    for dy in (-1, 0, 1):
        ny = y0 + dy
        if ny < 0 or ny >= h:
            continue
        for dx in (-1, 0, 1):
            nx = x0 + dx
            if nx < 0 or nx >= w:
                continue
            off_n = (ny * w + nx) * 4
            d = _color_delta_sq(
                buf[off0],
                buf[off0 + 1],
                buf[off0 + 2],
                buf[off0 + 3],
                other_buf[off_n],
                other_buf[off_n + 1],
                other_buf[off_n + 2],
                other_buf[off_n + 3],
            )
            if d <= max_delta:
                return True

    return False


def _write_pixelmatch_diff(
    baseline: Image.Image, diff_pixels: list[int], w: int, h: int, diff_path: Path
) -> None:
    dimmed = baseline.convert("L").convert("RGBA")
    diff_img = Image.blend(dimmed, Image.new("RGBA", (w, h), (0, 0, 0, 0)), 0.3)
    pixels = diff_img.load()
    assert pixels is not None
    for idx in diff_pixels:
        x, y = idx % w, idx // w
        pixels[x, y] = (255, 0, 0, 255)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_img.save(diff_path)

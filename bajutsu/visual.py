"""Visual regression — pixel-level image comparison for deterministic assertions.

Compares a captured screenshot against a stored baseline image.  Differences are
reported as a percentage of changed pixels; an optional threshold allows minor
rendering variance.  Exclude regions (e.g. the status bar or clock) are masked
before comparison so dynamic content does not cause false failures.

Requires ``Pillow`` (``pip install bajutsu[visual]``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from bajutsu.scenario import ExcludeRegion


@dataclass(frozen=True)
class CompareResult:
    ok: bool
    diff_pct: float  # percentage of pixels that differ (0.0-100.0)
    reason: str = ""


def compare_images(
    actual_path: Path,
    baseline_path: Path,
    *,
    threshold: float = 0.0,
    exclude: list[ExcludeRegion] | None = None,
    diff_path: Path | None = None,
) -> CompareResult:
    """Compare *actual_path* against *baseline_path* and return the result.

    *threshold* is the maximum allowed diff percentage (0.0 = exact match).
    *exclude* regions are zeroed out in both images before comparison.
    If *diff_path* is given and images differ, a diff visualization is written there.
    """
    actual = Image.open(actual_path).convert("RGBA")
    baseline = Image.open(baseline_path).convert("RGBA")

    if actual.size != baseline.size:
        return CompareResult(
            ok=False,
            diff_pct=100.0,
            reason=f"size mismatch: actual {actual.size} vs baseline {baseline.size}",
        )

    # Apply exclude masks — zero out the regions in both images
    if exclude:
        for r in exclude:
            box = (int(r.x), int(r.y), int(r.x + r.w), int(r.y + r.h))
            mask_fill = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
            actual.paste(mask_fill, box)
            baseline.paste(mask_fill, box)

    # Pixel-level comparison via ImageChops (fast C-level diff)
    diff = ImageChops.difference(actual, baseline)

    # Count non-zero pixels (any channel differs)
    total_pixels = actual.size[0] * actual.size[1]
    diff_bw = diff.convert("L")  # grayscale: 0 = identical
    diff_count = sum(1 for px in diff_bw.tobytes() if px > 0)

    if diff_count == 0:
        return CompareResult(ok=True, diff_pct=0.0)

    diff_pct = (diff_count / total_pixels) * 100.0

    if diff_path is not None:
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff.save(diff_path)

    ok = diff_pct <= threshold
    reason = "" if ok else f"visual diff {diff_pct:.2f}% exceeds threshold {threshold:.2f}%"
    return CompareResult(ok=ok, diff_pct=diff_pct, reason=reason)

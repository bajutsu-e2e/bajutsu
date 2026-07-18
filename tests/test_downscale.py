"""Tests for the record screenshot downscale helper (BE-0193).

The helper right-sizes the authoring screenshot before it reaches the model: it caps the
long edge at ``MAX_IMAGE_LONG_EDGE`` (a property of the model's image handling, not the app),
downscaling only and preserving aspect ratio so the normalized ``tap_point`` coordinates map
to exactly the same screen point. No Simulator needed — pure pixel operations over synthetic
PNGs.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from bajutsu.evidence.visual import downscale_png
from bajutsu.screenshots import MAX_IMAGE_LONG_EDGE, screenshot_bytes


def _png(width: int, height: int) -> bytes:
    """A synthetic PNG of the given pixel size.

    Uses Pillow's C-implemented ``effect_noise`` so even a multi-megapixel image is generated
    quickly (a pure-Python per-pixel loop would dominate the suite's runtime at these sizes).
    """
    img = Image.effect_noise((width, height), 100).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def test_landscape_caps_the_width() -> None:
    out = downscale_png(_png(3000, 2000), 1568)
    w, h = _size(out)
    assert w == 1568
    assert h == round(2000 * 1568 / 3000)


def test_portrait_caps_the_height() -> None:
    out = downscale_png(_png(2000, 3000), 1568)
    w, h = _size(out)
    assert h == 1568
    assert w == round(2000 * 1568 / 3000)


def test_square_caps_both_edges() -> None:
    out = downscale_png(_png(3000, 3000), 1568)
    assert _size(out) == (1568, 1568)


def test_aspect_ratio_preserved() -> None:
    # The tap_point invariance guard: a preserved aspect ratio means normalized [0,1]
    # coordinates map to the identical screen point on the downscaled image. Integer-pixel
    # rounding admits a sub-pixel discrepancy, so both edges scale by the same factor to
    # within one pixel.
    src_w, src_h = 2400, 1600
    out = downscale_png(_png(src_w, src_h), 1568)
    w, h = _size(out)
    scale = 1568 / src_w
    assert w == 1568
    assert h == round(src_h * scale)


def test_small_image_passes_through_byte_for_byte() -> None:
    # Already within the cap: no re-encode, no upscale — the exact input bytes come back.
    data = _png(800, 600)
    assert downscale_png(data, 1568) is data


def test_exactly_at_cap_passes_through() -> None:
    data = _png(1568, 1000)
    assert downscale_png(data, 1568) is data


def test_output_stays_png() -> None:
    out = downscale_png(_png(3000, 2000), 1568)
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "PNG"


def test_extreme_aspect_ratio_clamps_short_edge_to_one_pixel() -> None:
    # A 1x5000 sliver would round the short edge to 0 without the clamp, raising in resize.
    out = downscale_png(_png(1, 5000), 1568)
    assert _size(out) == (1, 1568)


def test_non_positive_cap_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        downscale_png(_png(100, 100), 0)


class _ScreenshotDriver:
    """A minimal driver stand-in that writes an oversized PNG when asked to screenshot.

    ``screenshot_bytes`` only calls ``driver.screenshot(path)``, so the authoring capture path
    can be exercised end to end without a Simulator.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(self._data)


def test_authoring_capture_respects_the_cap() -> None:
    driver = _ScreenshotDriver(_png(3000, 2000))
    out = screenshot_bytes(driver)  # type: ignore[arg-type]
    assert out is not None
    w, h = _size(out)
    assert max(w, h) == MAX_IMAGE_LONG_EDGE

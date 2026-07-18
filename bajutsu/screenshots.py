"""Screenshot capture and pixel-coordinate helpers shared across the AI paths.

Capturing a bounded PNG of the current screen (`screenshot_bytes`) and mapping a
model's pixel coordinates onto the device's normalized `[0,1]` space (`png_size`,
`fraction`) are needed by more than one caller: the record loop, the alert guard,
the crawl guide, the tab locator, and enrichment. They lived as underscore-prefixed
helpers in `record.py` / `alerts.py`, but every one had a cross-module caller, so the
leading underscore misrepresented the public surface. They are gathered here so the
name matches how they are used (BE-0246).

None of this reaches the deterministic `run` / CI verdict path — these serve the AI
authoring and investigation paths only.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from bajutsu.drivers import base

_logger = logging.getLogger(__name__)

# The long-edge cap for the authoring screenshot (BE-0193). Anthropic bills an image by its pixel
# dimensions and derives no benefit above ~1568px on the long edge, so a full-resolution Simulator
# capture pays for pixels the model discards. A global constant, not a `targets.<name>` knob: the
# right resolution is a property of the model's image handling, not of the app under test.
MAX_IMAGE_LONG_EDGE = 1568


def _downscaled(data: bytes) -> bytes:
    """Right-size the captured PNG to `MAX_IMAGE_LONG_EDGE` before it reaches the model (BE-0193).

    Applied here, on the shared authoring capture path, so every backend (iOS and web) hands the
    model the same bounded image and the vendor-neutral adapter stays a pure translator (BE-0104).
    Pillow lives in the `visual` extra, which `record` does not require; without it the full-
    resolution bytes pass through unchanged (the screenshot is best-effort either way) and the
    provider's own server-side downscale still bounds the cost. The downscale is an optimization,
    not a correctness requirement: a capture Pillow cannot decode is sent as-is rather than dropped.
    """
    try:
        from bajutsu.evidence.visual import downscale_png

        return downscale_png(data, MAX_IMAGE_LONG_EDGE)
    except ImportError:
        _logger.debug("Pillow not installed; sending the screenshot at full resolution")
        return data
    except Exception as exc:
        _logger.debug("screenshot downscale skipped (%s); sending at full resolution", exc)
        return data


def screenshot_bytes(driver: base.Driver) -> bytes | None:
    """Capture a PNG of the current screen as bytes (best-effort).

    Returns None on both a genuinely empty capture and a failure — callers treat the
    screenshot as optional and continue either way — but logs a warning when the capture
    *fails* (a stale simulator, a permissions error, a full disk), so a real failure stays
    distinguishable from "there was nothing to capture" instead of vanishing into None.
    """
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        driver.screenshot(path)
        data = Path(path).read_bytes() or None
        return _downscaled(data) if data is not None else None
    except Exception as exc:
        _logger.warning("screenshot capture failed: %s", exc, exc_info=True)
        return None
    finally:
        # Clean up on both paths: on a capture failure the temp file is already created
        # (delete=False), so without this a repeated failure leaks PNGs into the temp dir.
        if path is not None:
            Path(path).unlink(missing_ok=True)


def png_size(png: bytes) -> tuple[int, int]:
    """(width, height) in pixels from the PNG IHDR header, or (0, 0) if not parseable."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return (0, 0)


def fraction(value: float, size: int) -> float:
    """Map a coordinate to [0,1]: a pixel value (>1) over a known size, else as-is."""
    frac = value / size if size > 0 and value > 1.0 else value
    return min(1.0, max(0.0, frac))

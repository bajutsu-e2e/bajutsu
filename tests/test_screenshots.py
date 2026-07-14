"""Tests for the shared screenshot / pixel-coordinate helpers (BE-0246 Unit 6).

These functions used to be underscore-prefixed helpers in ``record.py`` / ``alerts.py``
with cross-module callers; the move to ``bajutsu.screenshots`` makes the public surface
honest. The behavior is unchanged, so these tests pin the same contract — the best-effort
capture (returns bytes / None, distinguishing an empty capture from a failure) and the two
pure pixel-coordinate helpers.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

import pytest
from conftest import ShotDriver

from bajutsu.drivers.fake import FakeDriver
from bajutsu.screenshots import fraction, png_size, screenshot_bytes


def _png(width: int, height: int) -> bytes:
    """A minimal PNG whose IHDR carries the given dimensions (the rest need not be valid)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", width, height)
    return sig + b"\x00\x00\x00\x0dIHDR" + ihdr + b"\x08\x06\x00\x00\x00"


def test_png_size_reads_ihdr_dimensions() -> None:
    assert png_size(_png(320, 640)) == (320, 640)


def test_png_size_returns_zero_for_non_png() -> None:
    assert png_size(b"not a png") == (0, 0)


def test_fraction_maps_pixel_over_known_size() -> None:
    assert fraction(160.0, 320) == 0.5


def test_fraction_passes_through_normalized_value() -> None:
    # A value already in [0,1] (<= 1.0) is treated as a fraction, not a pixel count.
    assert fraction(0.25, 320) == 0.25


def test_fraction_clamps_out_of_range() -> None:
    assert fraction(400.0, 320) == 1.0
    assert fraction(-5.0, 320) == 0.0


# --- screenshot_bytes (the unified best-effort capture helper, BE-0132) ---


class _RaisingShotDriver(FakeDriver):
    """A FakeDriver whose screenshot fails — the stale-simulator / full-disk case."""

    attempted_path: str | None = None

    def screenshot(self, path: str) -> None:
        self.attempted_path = path  # record it so the test can assert the temp file was cleaned up
        raise RuntimeError("simulator gone")


def test_screenshot_bytes_returns_captured_png() -> None:
    assert screenshot_bytes(ShotDriver([])) == b"\x89PNG\r\n\x1a\n fake"


def test_screenshot_bytes_none_when_nothing_captured(caplog: pytest.LogCaptureFixture) -> None:
    # The base FakeDriver writes no bytes: a genuine empty capture, not a failure — so it
    # returns None without logging a warning (the empty case must stay distinct from failure).
    with caplog.at_level(logging.WARNING, logger="bajutsu.screenshots"):
        assert screenshot_bytes(FakeDriver([])) is None
    assert not caplog.records


def test_screenshot_bytes_surfaces_failure_instead_of_swallowing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A real capture failure must not be indistinguishable from an empty capture: it returns
    # None (best-effort, callers continue) but leaves a warning in the log so it is visible.
    driver = _RaisingShotDriver([])
    with caplog.at_level(logging.WARNING, logger="bajutsu.screenshots"):
        assert screenshot_bytes(driver) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "simulator gone" in caplog.text
    # The temp file is cleaned up even on failure, so repeated failures don't leak PNGs.
    assert driver.attempted_path is not None and not Path(driver.attempted_path).exists()

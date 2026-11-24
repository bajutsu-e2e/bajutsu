"""The seam for a backend whose gesture coordinates are not in points (BE-0496)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DisplayScaleProvider(Protocol):
    """A backend that reports how many of its own coordinate units make up one point.

    The notification banner's swipe distances were measured in iOS points (BE-0416), and a backend
    that addresses raw display pixels would otherwise travel them as pixels: on a high-density screen
    that is too short a flick to dismiss anything. adb reports `wm density` / 160, the pixels in one
    density-independent pixel, which spans nearly the same physical length as an iOS point. A backend
    without this protocol already works in points, so the caller scales by `1.0`.
    """

    def display_scale(self) -> float:
        """Device coordinate units per point."""

"""The error raised when the device refused a two-finger gesture partway through."""

from __future__ import annotations

from .adb_resident_error import AdbResidentError


class AdbGestureRejected(AdbResidentError):
    """The resident server began a two-finger gesture and the platform rejected one of its events.

    The events before the rejected one may already have reached the screen — a finger down, a few
    moves — so a second gesture injected on top of this one is ruled out. Held apart from its base,
    whose other faults (a channel that could not be reached before the request went out) injected
    nothing and are safe to fall back from: the driver fails the step on this one instead.
    """

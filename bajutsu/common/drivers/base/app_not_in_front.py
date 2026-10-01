"""The error a device-group member's driver raises when another app holds the screen (BE-0447)."""

from __future__ import annotations

from .element_not_found import ElementNotFound


class AppNotInFront(ElementNotFound):
    """The read showed another app's tree, so nothing in it may be resolved for this target.

    A subclass of `ElementNotFound` so every step that fails on a missing element fails on this too,
    with the cause named — but raised from the read itself, never returned as an empty tree, so a
    negative check (`wait: gone`, an absent-element assertion) can never pass on a screen it was not
    looking at. A driver's own handler that treats a missing element as "scroll and look again" or
    "not tappable" re-raises this one first, so it never actuates against another app's screen.
    """

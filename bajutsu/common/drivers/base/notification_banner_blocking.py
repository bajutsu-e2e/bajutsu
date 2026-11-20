"""The error raised when a notification banner covers a gesture's point and will not clear."""

from __future__ import annotations


class NotificationBannerBlocking(Exception):
    """A notification banner covered the point a gesture was about to touch, past the clearance wait.

    Deliberately not an `ElementNotTappable`: the tap family answers that with a bounded scroll, and
    a banner anchored to the screen moves with no scroll — scrolling would only move the app under
    it, and a banner that left on its own mid-recovery would let the retried tap land on a screen the
    scenario never asked to scroll (BE-0496).
    """

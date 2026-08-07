"""Shared endpoint math for a directional gesture, used by both `swipe`/`drag` and `scroll`.

Split out from `gestures.py` so `scroll.py` can depend on it without `gestures.py` in turn needing to
import from `scroll.py` — the cycle `gestures.py`'s own import of `scroll_until_tappable` would
otherwise create.
"""

from __future__ import annotations

from bajutsu.drivers import base

# The default directional swipe travels a fraction of the screen, not a fixed coordinate count, so
# it scrolls a consistent proportion of any device regardless of its coordinate unit — iOS reports
# frames in points, Android in raw pixels, web in CSS pixels. A fixed count scrolls far less of a
# dense Android screen (2400px) than of an iOS one (~900pt), so a swipe sized for iOS barely moves an
# Android list; a screen fraction keeps the scroll reach at parity across backends (BE-0208). 0.125
# reproduces the previous 100-unit nudge on the historical 800-tall reference screen.
_SWIPE_FRACTION = 0.125  # default travel as a fraction of the screen when `amount` isn't given
_SWIPE_MARGIN = 4.0  # keep both gesture endpoints this far inside the screen edges


def _scroll_gesture(
    center: base.Point, direction: str, amount: float | None, screen: base.Point
) -> tuple[base.Point, base.Point]:
    """The (from, to) points for a directional swipe that travels `amount` of the screen.

    `amount` is a fraction of the screen (height for up/down, width for left/right); ``None`` uses
    the default fraction. The gesture *begins on* `center` when there is room, and travels a segment of
    that length in the direction (`up`/`left` toward the smaller coordinate), so a bigger `amount`
    scrolls proportionally further. Beginning on the element — rather than centering the travel
    across it — is what lets a swipe grab a small handle (e.g. a resize divider) it would otherwise
    straddle and miss. Only when a travel would overrun a screen edge does the segment slide back on
    (moving the start off `center` in that case), which keeps the travelled distance intact.
    """
    cx, cy = center
    sw, sh = screen
    vertical = direction in ("up", "down")
    dim = sh if vertical else sw
    dist = (amount if amount is not None else _SWIPE_FRACTION) * dim
    span = min(dist, max(0.0, dim - 2 * _SWIPE_MARGIN))
    anchor = cy if vertical else cx
    start = min(max(anchor, _SWIPE_MARGIN), dim - _SWIPE_MARGIN)
    end = start - span if direction in ("up", "left") else start + span
    if end < _SWIPE_MARGIN:
        start += _SWIPE_MARGIN - end
        end = _SWIPE_MARGIN
    elif end > dim - _SWIPE_MARGIN:
        start -= end - (dim - _SWIPE_MARGIN)
        end = dim - _SWIPE_MARGIN
    return ((cx, start), (cx, end)) if vertical else ((start, cy), (end, cy))

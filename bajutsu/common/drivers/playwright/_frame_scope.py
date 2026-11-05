"""Where an element read out of an iframe lives, so an action can reach it in its own frame."""

from __future__ import annotations

from typing import Any, NamedTuple

from bajutsu.common.drivers import base


class _FrameScope(NamedTuple):
    """The frame an iframe element was read from, and the `<iframe>` chain that leads to it.

    `query()` reports every frame's elements in main-page CSS pixels, so a click at the element's
    center needs no translation. The occlusion check and `selectOption` do: `elementFromPoint` only
    sees its own document, so they run in `frame` at the point minus `origin`. `hosts` is each
    frame on the way down (outermost first) with the main-page origin of the document holding its
    `<iframe>`, so the check can confirm no cover in any ancestor swallows the click. Frames, not
    element handles, are kept: a handle pins its node until disposed, and a snapshot is discarded.
    """

    frame: Any
    origin: base.Point
    hosts: tuple[tuple[Any, base.Point], ...]

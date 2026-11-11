"""Read the Android system photo picker's state out of a UI Automator tree (`selectPhotos`)."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

from bajutsu.common.drivers import base

from ._functions import _bounds

# The picker is the MediaProvider mainline module's own activity: Google builds ship it under the
# `google` package, AOSP builds under the plain one. Matching the package as well as the id keeps an
# app's own view that happens to share an id (`icon_thumbnail` is not an unusual name) out of the grid.
PICKER_PACKAGES = frozenset(
    {"com.google.android.providers.media.module", "com.android.providers.media.module"}
)
_THUMBNAIL = "icon_thumbnail"
# A check mark sits on every cell only when the picker allows more than one image, so its presence
# is how the driver tells a multi-selection picker (confirm with `button_add`) from a single-selection
# one (closes on its first tap) without reading the launching app's intent.
_CHECK = "icon_check"
_ADD = "button_add"


@dataclass(frozen=True)
class PickerCell:
    """One grid cell: the thumbnail's frame and whether the picker marks it selected."""

    frame: base.Frame
    selected: bool


@dataclass(frozen=True)
class PickerState:
    """What one tree read shows of the photo picker.

    `cells` are in reading order (top to bottom, then left to right), the order `selectPhotos`
    indices address. `present` is False once no picker node remains, which is how the driver knows
    the picker has handed its result back to the app.
    """

    present: bool
    cells: tuple[PickerCell, ...]
    multi_select: bool
    add_button: base.Frame | None


def _local_id(node: ET.Element) -> str:
    return (node.get("resource-id") or "").rsplit("/", 1)[-1]


def read_picker_state(root: ET.Element | None) -> PickerState:
    """The picker's grid, selection mode, and confirm button as `root` shows them."""
    nodes = [] if root is None else [n for n in root.iter() if n.get("package") in PICKER_PACKAGES]
    cells: list[PickerCell] = []
    multi = False
    add: base.Frame | None = None
    for n in nodes:
        local = _local_id(n)
        if local == _THUMBNAIL:
            frame = _bounds(n.get("bounds") or "")
            # A zero-area thumbnail is a recycled cell scrolled out of view; tapping its centre would
            # land on whatever sits at that point instead.
            if frame[2] > 0 and frame[3] > 0:
                cells.append(PickerCell(frame=frame, selected=n.get("selected") == "true"))
        elif local == _CHECK:
            multi = True
        elif local == _ADD:
            add = _bounds(n.get("bounds") or "")
    # Reading order from the frames rather than document order: a recycling grid view does not
    # promise to emit its children in the order it lays them out.
    cells.sort(key=lambda c: (c.frame[1], c.frame[0]))
    return PickerState(present=bool(nodes), cells=tuple(cells), multi_select=multi, add_button=add)

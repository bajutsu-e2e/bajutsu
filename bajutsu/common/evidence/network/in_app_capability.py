"""A piece of Bajutsu's own in-app instrumentation the control channel may address (BE-0365)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class InAppCapability(StrEnum):
    """A piece of bajutsu's own in-app instrumentation the control channel may address (BE-0365).

    Closed on purpose, and that is the boundary rather than a comment about it: the channel controls
    what bajutsu put inside the app, never the application's own state. A command that seeded app
    data or drove navigation would move per-app knowledge into the tool (prime directive 3), so a
    new capability is argued for here instead of being named as a free string at a call site.
    """

    TOUCH_VISUALIZATION = "touch_visualization"  # the touch markers BE-0371 draws
    STUB_TABLE = "stub_table"  # the mocked responses BajutsuKit serves (`BAJUTSU_MOCKS`)


# The capabilities whose whole state is on/off, so `enqueue_command(..., enabled=)` can address them.
# A capability outside it carries a state of its own shape and has its own enqueue call.
ToggleCapability = Literal[InAppCapability.TOUCH_VISUALIZATION]

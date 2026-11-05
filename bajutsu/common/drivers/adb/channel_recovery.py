"""What a driver can ask of a resident channel it has stopped using."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelRecovery:
    """The lease's handle on its resident server, for a driver that has fallen back to `uiautomator dump`.

    Attributes:
        live: Whether the server is up now. A device group's drivers share one server, so another
            member may have brought it back; a dump taken beside a live server reads an empty tree.
        revive: Start the server again; True when it came up.
        drain_notes: What the channel recovered from since the last call, for the run's evidence.
    """

    live: Callable[[], bool]
    revive: Callable[[], bool]
    drain_notes: Callable[[], list[str]]

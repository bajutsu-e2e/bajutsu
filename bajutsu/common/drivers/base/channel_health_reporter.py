"""The seam for a backend whose read channel can degrade during a run."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ChannelHealthReporter(Protocol):
    """A backend that reports when its read channel recovered from a fault or fell back.

    A narrow opt-in, like `ActuationReporter`: Android alone has a second way to read, so a run there
    can pass on a different read path from the one it started on. Evidence only; nothing on the verdict
    path reads it.
    """

    def drain_channel_notes(self) -> list[str]:
        """What happened to the channel since the last call, oldest first."""

    def channel_degraded(self) -> str | None:
        """Why the channel is degraded right now, or None while it is healthy."""

"""One group event a delivering process holds a claim on (BE-0500)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class GroupEventClaim:
    """A pending group event claimed for delivery, with what its adapter needs.

    Attributes:
        token: The claim's own id; only a settle that names it moves the row.
        occurred_at: When the event happened, which the adapter sends as its time.
        attempts: The deliveries already tried and failed.
        reference: What the latest earlier event of the same group and sink returned, or None.
        snapshot: A `completed` event's rendering input, once built.
        notify: The group's selections, its target, and the repository its checks go to.
    """

    id: str
    token: str
    group_id: str
    org_id: str
    sink: str
    event: str
    occurred_at: datetime
    attempts: int
    reference: str | None
    snapshot: dict[str, Any] | None
    notify: dict[str, Any]

    def head_sha(self) -> str:
        """The commit this claim's sink selection names."""
        return str(next(s["headSha"] for s in self.notify["sinks"] if s["sink"] == self.sink))

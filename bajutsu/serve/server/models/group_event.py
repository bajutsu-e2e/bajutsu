"""The group_events table: one row per event a `run-set` group owes one of its sinks (BE-0500)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from ._shared import _JSON
from .base import Base


class GroupEvent(Base):
    __tablename__ = "group_events"
    __table_args__ = (
        # Each event fires at most once per group and sink; a second write of it is ignored.
        Index("ux_group_events_group_sink_event", "group_id", "sink", "event", unique=True),
        Index("ix_group_events_state_next", "state", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    group_id: Mapped[str]
    sink: Mapped[str]
    # queued | started | completed
    event: Mapped[str]
    # When the event happened, which the adapter sends rather than the time of its own delivery.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # pending | delivered | abandoned
    state: Mapped[str] = mapped_column(default="pending")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # A delivering process's claim, which expires so a process that died mid-delivery loses it.
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    claim_token: Mapped[str | None] = mapped_column(default=None)
    # What the adapter returned for later events of the group and sink: a check run id.
    reference: Mapped[str | None] = mapped_column(default=None)
    # A `completed` event's rendering input, built once when the row is first claimed.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(_JSON, default=None)

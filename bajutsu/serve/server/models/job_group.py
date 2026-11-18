"""The job_groups table: one row per `run-set` request, naming the jobs it registered (BE-0484)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from ._functions import _created_at
from .base import Base


class JobGroup(Base):
    __tablename__ = "job_groups"
    # A caller's retry finds its group by key, so one org holds each key once (BE-0484).
    __table_args__ = (Index("ux_job_groups_org_key", "org_id", "key", unique=True),)

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(default="")
    # The caller's `groupKey`, or one the server generated when the request named none.
    key: Mapped[str]
    # What the request asked to run, so a repeated key tells a retry from a different request. None
    # on a group written before BE-0484 unit 3, whose key no request can repeat.
    digest: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = _created_at()

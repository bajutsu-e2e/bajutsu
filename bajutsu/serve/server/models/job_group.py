"""The job_groups table: one row per `run-set` request, naming the jobs it registered (BE-0484)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, false
from sqlalchemy.dialects.postgresql import JSONB
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
    # The first failing job cancels the group's unfinished jobs (BE-0499).
    fail_fast: Mapped[bool] = mapped_column(default=False, server_default=false())
    # The sinks the request selected, its target, and the repository its machine session verified
    # (BE-0500). SQL NULL, not JSON null, when it selected none, so a lock can filter on it.
    notify: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"), default=None
    )
    created_at: Mapped[datetime] = _created_at()

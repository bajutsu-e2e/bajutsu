"""The job_groups table: one row per `run-set` request, naming the jobs it registered (BE-0484)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from ._functions import _created_at
from .base import Base


class JobGroup(Base):
    __tablename__ = "job_groups"

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(default="")
    # Server-generated until BE-0484 unit 3 lets the caller choose it; that unit adds the unique
    # index on (org_id, key) with the request digest.
    key: Mapped[str]
    created_at: Mapped[datetime] = _created_at()

"""The API-key table: one row per organization-scoped key issued to a script."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from ._functions import _created_at
from .base import Base


class ApiKey(Base):
    """One API key (BE-0509). The secret half is never stored, only its SHA-256 hash.

    A revoked row stays: the identifier must go on being *recognized* as a key, so a revoked one
    answers 401 rather than falling through to the shared-token check.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str]
    role: Mapped[str]
    secret_hash: Mapped[str]
    # A plain column, not an FK to `users.id` like `runs.deleted_by`: a shared-token admin has no
    # identity, and the row must outlive whoever issued it.
    created_by: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

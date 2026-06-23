"""SQLAlchemy ORM models for the hosted backend's system of record (BE-0015 7a).

Imported only when a database-backed `Repository` is assembled (see `db.py`), never on the default
`serve`/CLI path — so SQLAlchemy stays behind the optional `db` extra. `org_id` threads through
every table so 7c's per-org scoping and quotas can filter on it; only the variable manifest summary
and audit detail use JSON (JSONB on Postgres, plain JSON on SQLite), keeping the relational core in
ordinary columns."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, DateTime

# JSONB on Postgres (production); the portable JSON type on SQLite (the gate).
_JSON = JSON().with_variant(JSONB, "postgresql")


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Base(DeclarativeBase):
    pass


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"))
    email: Mapped[str] = mapped_column(unique=True)
    github_login: Mapped[str | None] = mapped_column(default=None)
    # Default editor to match the role policy (an allowlisted user can run); admins/viewers come
    # from the env lists, recomputed on each login. Aligned across model / migration / upsert (7c-2).
    role: Mapped[str] = mapped_column(server_default="editor")  # viewer | editor | admin
    created_at: Mapped[datetime] = _created_at()


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"))
    name: Mapped[str]  # the config target name
    created_at: Mapped[datetime] = _created_at()


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), default=None)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), default=None)
    status: Mapped[str] = mapped_column(default="")
    ok: Mapped[bool | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = _created_at()
    summary: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), default=None)
    action: Mapped[str] = mapped_column(default="")
    target: Mapped[str] = mapped_column(default="")
    at: Mapped[datetime] = _created_at()
    detail: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)

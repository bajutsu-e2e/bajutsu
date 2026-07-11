"""SQLAlchemy ORM models for the hosted backend's system of record (BE-0015 7a).

Imported only when a database-backed `Repository` is assembled (see `db.py`), never on the default
`serve`/CLI path — so SQLAlchemy stays behind the optional `db` extra. `org_id` threads through
every table so 7c's per-org scoping and quotas can filter on it; only the variable manifest summary
and audit detail use JSON (JSONB on Postgres, plain JSON on SQLite), keeping the relational core in
ordinary columns."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, UniqueConstraint, func
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
    # Run provenance mirrored from the run's manifest.json (BE-0049 stamp), so cross-run flakiness
    # can group by scenario identity straight from the DB (BE-0220) without re-reading every
    # manifest from object storage. Null for a pre-provenance run — unstamped, so ungroupable.
    scenario_hash: Mapped[str | None] = mapped_column(default=None)
    tool_version: Mapped[str | None] = mapped_column(default=None)
    git_revision: Mapped[str | None] = mapped_column(default=None)

    # scenario_hash is the flakiness grouping key (GROUP BY scenario_hash, then per-scenario name).
    __table_args__ = (Index("ix_runs_scenario_hash", "scenario_hash"),)


class JobRecord(Base):
    __tablename__ = "jobs"
    # The lease/reclaim hot paths filter on status (and leased_at for reclaim), swept on every poll,
    # so these composite indexes keep them off a full-table scan as the jobs table grows. The second
    # (status, created_at) serves the capability-aware lease scan (BE-0166), which reads queued rows
    # `ORDER BY created_at` — the index provides both the filter and the order.
    __table_args__ = (
        Index("ix_jobs_status_leased_at", "status", "leased_at"),
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(default="")
    spec: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)
    # Capability tokens a worker must advertise to lease this job (BE-0166): its platform axis plus
    # the target's `requires`. The routing key lives on the row (not a new store); the lease filter
    # serves a job only to a worker whose advertised set is a superset. Empty = any worker (a job
    # with no declared requirement, e.g. triage), preserving the pre-routing single-queue behavior.
    capabilities: Mapped[list[str]] = mapped_column(_JSON, default=list)
    status: Mapped[str] = mapped_column(default="queued")  # queued | leased | done | failed
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    leased_by: Mapped[str | None] = mapped_column(default=None)
    # How many times this job has been leased and lost (lease expiry) — a poison job that keeps
    # killing its worker is failed once it hits the attempt cap rather than re-queued forever.
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    result: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)
    created_at: Mapped[datetime] = _created_at()


class WorkerRecord(Base):
    """A worker's advertised capabilities and liveness, refreshed on every lease poll (BE-0166).

    The post-completion worker model (BE-0106) keeps no standing worker table — a worker is known
    only transiently as `jobs.leased_by`. Capability routing needs one more thing: what the *live*
    pool can serve, so the control plane can tell an operator a queued job is **unroutable** (no
    worker advertises its required capabilities) rather than letting it hang silently. The lease
    path upserts this row each poll (`last_seen` = the poll clock); a worker is "live" while
    `last_seen` is within the lease timeout, the same freshness window heartbeats use.
    """

    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(primary_key=True)  # the worker_id it leases under
    capabilities: Mapped[list[str]] = mapped_column(_JSON, default=list)
    last_seen: Mapped[datetime] = _created_at()  # refreshed on every lease poll / heartbeat


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(primary_key=True)
    identity: Mapped[str | None] = mapped_column(default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Secret(Base):
    """A per-org operator secret, encrypted at rest (BE-0136 write-once secrets). Keyed by
    (org_id, name) — one value per named secret per org. Only the ciphertext is stored; the
    plaintext exists only transiently inside the store that decrypts it, never in a column."""

    __tablename__ = "secrets"

    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    name: Mapped[str] = mapped_column(primary_key=True)  # the logical secret name, e.g. "aiApiKey"
    ciphertext: Mapped[str]  # a Fernet token (authenticated encryption), never the plaintext
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), default=None)
    updated_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), default=None)
    action: Mapped[str] = mapped_column(default="")
    target: Mapped[str] = mapped_column(default="")
    at: Mapped[datetime] = _created_at()
    detail: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)

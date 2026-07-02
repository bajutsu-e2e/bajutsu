"""The `Repository` seam: the hosted backend's system of record (BE-0015 7a).

Shaped like the other server seams (`object_store.py`): a `Protocol`, a SQLAlchemy implementation,
and an env-driven factory — with SQLAlchemy and the ORM models lazy-imported inside the functions
that need them, so the default `serve`/CLI path never loads them (the import guard locks this).
7a implements only the `runs` methods; `RunRecord` is the boundary type so ORM rows never leak
past the seam."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from bajutsu.serve.server.models import Run


@dataclass
class LeasedJob:
    """A job that has been leased by a worker — the boundary type the seam hands out."""

    id: str
    org_id: str
    spec: dict[str, Any]


@dataclass
class RunRecord:
    """A run as the seam exchanges it — the relational core plus the JSON manifest summary."""

    id: str
    org_id: str
    status: str
    project_id: str | None = None
    created_by: str | None = None
    ok: bool | None = None
    created_at: datetime | None = None
    summary: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Repository(Protocol):
    """Persistence for the control plane. 7a covers runs; identity/audit land in 7b/7c."""

    def record_run(self, run: RunRecord) -> None:
        """Insert *run*, or update it in place when its id already exists (e.g. a status change)."""

    def get_run(self, run_id: str) -> RunRecord | None:
        """The run with *run_id*, or None if there is none."""

    def list_runs(self, *, org_id: str, limit: int = 50) -> list[RunRecord]:
        """An org's runs, newest first, capped at *limit*."""

    def ensure_org(self, org_id: str, *, slug: str, name: str) -> None:
        """Create the org if it does not exist yet (idempotent) — 7c-1's single default org."""

    def upsert_user(
        self, user_id: str, *, org_id: str, github_login: str, email: str, role: str = "editor"
    ) -> None:
        """Insert the user, or update it in place when its id already exists (an OAuth re-login),
        setting its *role* (recomputed from policy each login, BE-0015 7c-2)."""

    def user_role(self, user_id: str) -> str | None:
        """The user's role (viewer/editor/admin), or None if there is no such user."""

    def user_org(self, user_id: str) -> str | None:
        """The user's org id, or None if there is no such user (BE-0015 multi-tenancy)."""

    def record_audit(
        self, *, org_id: str, actor_id: str | None, action: str, target: str, detail: dict[str, Any]
    ) -> None:
        """Append an audit-log entry — who did what to which target, and when (server clock)."""

    def enqueue_job(self, job_id: str, org_id: str, spec: dict[str, Any]) -> None:
        """Insert a job with status ``queued``."""

    def lease_job(self, worker_id: str) -> LeasedJob | None:
        """Atomically lease the oldest queued job to *worker_id*, or return None."""

    def complete_job(self, job_id: str, result: dict[str, Any]) -> None:
        """Mark a leased job as ``done`` and store its *result*."""

    def fail_job(self, job_id: str, error: str) -> None:
        """Mark a leased job as ``failed`` with an error message."""

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return the job's status and result, or None if it does not exist."""


def _to_record(row: Run) -> RunRecord:
    return RunRecord(
        id=row.id,
        org_id=row.org_id,
        status=row.status,
        project_id=row.project_id,
        created_by=row.created_by,
        ok=row.ok,
        created_at=row.created_at,
        summary=dict(row.summary),
    )


class SqlRepository:
    """A SQLAlchemy-backed `Repository`. Works against any engine SQLAlchemy supports — SQLite on
    the gate, Postgres in production — since the models pick JSONB only on Postgres."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_run(self, run: RunRecord) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        # `merge` upserts by primary key, so re-recording a run (e.g. a status change) updates it
        # rather than colliding. `created_at` is left to the server default unless given.
        fields: dict[str, Any] = {
            "id": run.id,
            "org_id": run.org_id,
            "status": run.status,
            "project_id": run.project_id,
            "created_by": run.created_by,
            "ok": run.ok,
            "summary": run.summary,
        }
        if run.created_at is not None:
            fields["created_at"] = run.created_at
        with Session(self._engine) as session:
            session.merge(Run(**fields))
            session.commit()

    def get_run(self, run_id: str) -> RunRecord | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        with Session(self._engine) as session:
            row = session.get(Run, run_id)
            return _to_record(row) if row is not None else None

    def list_runs(self, *, org_id: str, limit: int = 50) -> list[RunRecord]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        stmt = select(Run).where(Run.org_id == org_id).order_by(Run.created_at.desc()).limit(limit)
        with Session(self._engine) as session:
            return [_to_record(row) for row in session.scalars(stmt)]

    def ensure_org(self, org_id: str, *, slug: str, name: str) -> None:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            if session.get(Org, org_id) is not None:
                return
            session.add(Org(id=org_id, slug=slug, name=name))  # leave created_at to the default
            try:
                session.commit()
            except IntegrityError:
                # A concurrent login inserted it between the check and the commit — that's the
                # idempotent outcome we wanted, so swallow it.
                session.rollback()

    def upsert_user(
        self, user_id: str, *, org_id: str, github_login: str, email: str, role: str = "editor"
    ) -> None:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            if user is None:
                session.add(
                    User(
                        id=user_id,
                        org_id=org_id,
                        github_login=github_login,
                        email=email,
                        role=role,
                    )
                )
                try:
                    session.commit()
                    return
                except IntegrityError:
                    # A concurrent OAuth callback inserted the same user first; fall through to
                    # update the now-existing row instead of failing the login.
                    session.rollback()
                    user = session.get(User, user_id)
            if user is not None:  # update in place (a re-login) without disturbing created_at
                user.org_id, user.github_login, user.email, user.role = (
                    org_id,
                    github_login,
                    email,
                    role,
                )
                session.commit()

    def user_role(self, user_id: str) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            return user.role if user is not None else None

    def user_org(self, user_id: str) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            return user.org_id if user is not None else None

    def record_audit(
        self, *, org_id: str, actor_id: str | None, action: str, target: str, detail: dict[str, Any]
    ) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import AuditLog

        with Session(self._engine) as session:
            session.add(
                AuditLog(
                    id=uuid.uuid4().hex,
                    org_id=org_id,
                    actor_id=actor_id,
                    action=action,
                    target=target,
                    detail=detail,
                )
            )
            session.commit()

    def enqueue_job(self, job_id: str, org_id: str, spec: dict[str, Any]) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            session.add(JobRecord(id=job_id, org_id=org_id, spec=spec))
            session.commit()

    def lease_job(self, worker_id: str) -> LeasedJob | None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            stmt = (
                select(JobRecord)
                .where(JobRecord.status == "queued")
                .order_by(JobRecord.created_at)
                .limit(1)
            )
            if self._engine.dialect.name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)
            row = session.scalars(stmt).first()
            if row is None:
                return None
            row.status = "leased"
            row.leased_at = datetime.now(UTC)
            row.leased_by = worker_id
            session.commit()
            return LeasedJob(id=row.id, org_id=row.org_id, spec=dict(row.spec))

    def complete_job(self, job_id: str, result: dict[str, Any]) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            row = session.get(JobRecord, job_id)
            if row is not None:
                row.status = "done"
                row.result = result
                session.commit()

    def fail_job(self, job_id: str, error: str) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            row = session.get(JobRecord, job_id)
            if row is not None:
                row.status = "failed"
                row.result = {"error": error}
                session.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            row = session.get(JobRecord, job_id)
            if row is None:
                return None
            return {"status": row.status, "result": dict(row.result)}


def engine_from_url(url: str) -> Engine:
    """Build a SQLAlchemy engine for *url* (e.g. ``postgresql://…`` in production, ``sqlite://`` on
    the gate). SQLAlchemy is imported here so the default path never loads it."""
    from sqlalchemy import create_engine

    return create_engine(url)


def repository_from_env() -> SqlRepository | None:
    """A `SqlRepository` from ``BAJUTSU_DATABASE_URL``, or ``None`` when it is unset — so the
    server backend runs without a database until one is configured, and local never has one. The
    schema itself is owned by Alembic (7a-2), not created here."""
    url = os.environ.get("BAJUTSU_DATABASE_URL")
    if not url:
        return None
    return SqlRepository(engine_from_url(url))

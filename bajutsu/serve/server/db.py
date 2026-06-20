"""The `Repository` seam: the hosted backend's system of record (BE-0015 7a).

Shaped like the other server seams (`object_store.py`): a `Protocol`, a SQLAlchemy implementation,
and an env-driven factory — with SQLAlchemy and the ORM models lazy-imported inside the functions
that need them, so the default `serve`/CLI path never loads them (the import guard locks this).
7a implements only the `runs` methods; `RunRecord` is the boundary type so ORM rows never leak
past the seam."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from bajutsu.serve.server.models import Run


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

"""Open a repository from the environment, and convert rows to and from the seam's records."""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .org_record import OrgRecord
from .run_record import RunRecord

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from bajutsu.serve.server.models import Org, Run

    from .sql_repository import SqlRepository


def _to_org(row: Org) -> OrgRecord:
    # The membership columns are nullable — an org row that predates BE-0375, or one `ensure_org`
    # created at sign-in, holds NULL rather than `[]` (the model's `default` is Python-side only, so
    # it never reached those rows). Normalize here, once, so no reader has to.
    return OrgRecord(
        id=row.id,
        slug=row.slug,
        name=row.name,
        members=list(row.members or []),
        github_orgs=list(row.github_orgs or []),
        github_teams=list(row.github_teams or []),
        editor_teams=list(row.editor_teams or []),
        allowed_repositories=list(row.allowed_repositories or []),
        membership_seeded_at=row.membership_seeded_at,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        config_source=dict(row.config_source) if row.config_source is not None else None,
    )


def _to_record(row: Run) -> RunRecord:
    return RunRecord(
        id=row.id,
        org_id=row.org_id,
        status=row.status,
        created_by=row.created_by,
        ok=row.ok,
        created_at=row.created_at,
        summary=dict(row.summary),
        scenario_hash=row.scenario_hash,
        tool_version=row.tool_version,
        git_revision=row.git_revision,
        device_runtime=row.device_runtime,
        label=row.label,
        target=row.target,
        deleted_at=row.deleted_at,
        deleted_by=row.deleted_by,
    )


def _as_utc(dt: datetime) -> datetime:
    """Read a stored timestamp as UTC-aware. SQLite (the gate) hands back naive datetimes for a
    ``DateTime(timezone=True)`` column, so subtracting a UTC-aware ``now`` would raise; assume UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _age_seconds(now: datetime, then: datetime) -> float:
    return (now - _as_utc(then)).total_seconds()


def _rowcount(result: Any) -> int:
    """The rows a conditional UPDATE matched: whether a job transition won its race (BE-0449)."""
    return int(result.rowcount)


# How long a SQLite writer waits for another's lock before failing, in milliseconds. The server's
# request threads are the concurrent writers (BE-0449); a lease poll and a heartbeat landing together
# should queue, never surface "database is locked". Longer than the driver's own 5 s default, which a
# reclaim sweep over a large backlog can outlast.
SQLITE_BUSY_TIMEOUT_MS = 30_000


def engine_from_url(url: str) -> Engine:
    """Build a SQLAlchemy engine for *url* (e.g. ``postgresql://…`` in production, ``sqlite://`` on
    the gate). SQLAlchemy is imported here so the default path never loads it.

    A file-backed SQLite database gets write-ahead logging and a busy timeout on every connection
    (BE-0449): readers no longer block the writer, and two writers wait for each other instead of
    failing. An in-memory database has no file to log beside, so it is left as it is.
    """
    from sqlalchemy import create_engine, event

    engine = create_engine(url)
    if engine.dialect.name == "sqlite" and not _is_memory_sqlite(url):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.close()

    return engine


def _is_memory_sqlite(url: str) -> bool:
    from sqlalchemy.engine import make_url

    database = make_url(url).database
    return not database or database == ":memory:" or "mode=memory" in url


def upgrade_to_head(url: str) -> None:
    """Bring the database at *url* to the latest Alembic revision (BE-0449).

    The server never ran Alembic itself before: a hosted operator migrates as a deploy step. The
    default SQLite of a local start has no such step, so the server runs it on that database alone.
    *url* is handed to the migration environment directly, ahead of ``BAJUTSU_DATABASE_URL``, so a
    configured database is never migrated in its place.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    # configparser treats `%` as interpolation, which a URL-encoded password would trip.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


def _positive_env(name: str, raw: str, *, cast: Any) -> Any:
    """Parse an operator-facing positive-number env var defensively — a clear, variable-named error
    rather than a bare ValueError/TypeError. Non-numeric or non-positive values are rejected."""
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive number, got {raw!r}") from None
    # NaN/inf slip past `<= 0` (NaN compares False, inf is "positive"), so reject them explicitly —
    # a timedelta(seconds=nan) or an infinite cap is not a well-defined operator setting.
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {raw!r}")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def repository_from_env(default_url: str | None = None) -> SqlRepository | None:
    """A `SqlRepository` from ``BAJUTSU_DATABASE_URL``, else from *default_url*, else ``None``.

    The schema of a configured database is owned by Alembic (7a-2) and migrated by the operator, so
    it is never touched here. *default_url* is the database a local start supplies for itself
    (BE-0449); nobody else migrates it, so it is brought to the latest revision before use.
    """
    url = os.environ.get("BAJUTSU_DATABASE_URL")
    if not url:
        if default_url is None:
            return None
        upgrade_to_head(default_url)
        url = default_url
    kwargs: dict[str, Any] = {}
    if timeout := os.environ.get("BAJUTSU_LEASE_TIMEOUT_SECONDS"):
        kwargs["lease_timeout"] = timedelta(
            seconds=_positive_env("BAJUTSU_LEASE_TIMEOUT_SECONDS", timeout, cast=float)
        )
    if attempts := os.environ.get("BAJUTSU_LEASE_MAX_ATTEMPTS"):
        kwargs["max_attempts"] = _positive_env("BAJUTSU_LEASE_MAX_ATTEMPTS", attempts, cast=int)
    # Imported in the body, not at module load: the repository calls seven helpers from this
    # module, so rule 5 breaks the cycle the split creates on this factory's single edge back in.
    from .sql_repository import SqlRepository

    return SqlRepository(engine_from_url(url), **kwargs)

"""The server SessionStore implementation for the hosted backend.

`SqlSessionStore` (BE-0106) keeps login sessions in the same Postgres the system of record already
uses, so they survive a control-plane restart and span replicas. The engine is **injected**, so the
module imports no SQLAlchemy at the top — safe to import and unit-test without the optional extras;
the real engine is wired in by the server selection."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bajutsu.serve.sessions import SessionIdentity

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_DEFAULT_TTL = 604800  # seconds a session lives before it expires (7 days)


class SqlSessionStore:
    """SessionStore backed by a Postgres (or SQLite) sessions table (BE-0106).

    Sessions survive a restart and span replicas with no second stateful service. Expiry is enforced
    on read; the engine is injected so a test can hand in an in-memory SQLite. Beyond the login, a
    row carries what its sign-in observed on GitHub and the org the session acts as, so a switch
    outlives the process that served it (session-scoped org selection)."""

    def __init__(self, engine: Engine, *, ttl: int = _DEFAULT_TTL) -> None:
        self._engine = engine
        self._ttl = ttl

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _ensure_aware(dt: datetime) -> datetime:
        # SQLite returns naive datetimes; Postgres returns aware ones.
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt

    def issue(self, identity: str | None = None, *, context: SessionIdentity | None = None) -> str:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        sid = secrets.token_urlsafe(32)
        expires = self._now() + timedelta(seconds=self._ttl)
        # A context naming another login is dropped rather than stored, so a row's GitHub facts are
        # always the ones its own sign-in saw.
        record = context if context is not None and context.login == identity else None
        with Session(self._engine) as session:
            session.add(
                SessionRecord(
                    id=sid,
                    identity=identity,
                    expires_at=expires,
                    github_orgs=list(record.github_orgs) if record else None,
                    teams=list(record.teams) if record else None,
                    org=record.org if record else None,
                    role=record.role if record else None,
                )
            )
            session.commit()
        return sid

    def valid(self, sid: str) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        with Session(self._engine) as session:
            row = session.get(SessionRecord, sid)
            if row is None:
                return False
            return self._ensure_aware(row.expires_at) >= self._now()

    def identity(self, sid: str) -> str | None:
        record = self.context(sid)
        return record.login if record is not None else None

    def context(self, sid: str) -> SessionIdentity | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        with Session(self._engine) as session:
            row = session.get(SessionRecord, sid)
            if row is None or self._ensure_aware(row.expires_at) < self._now():
                return None
            return _record(row)

    def select_org(self, sid: str, org: str, role: str) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        with Session(self._engine) as session:
            row = session.get(SessionRecord, sid)
            if row is None or self._ensure_aware(row.expires_at) < self._now():
                return False
            row.org, row.role = org, role
            session.commit()
            return True

    def sessions_for_org(self, org: str) -> list[tuple[str, SessionIdentity]]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        now = self._now()
        with Session(self._engine) as session:
            rows = session.scalars(select(SessionRecord).where(SessionRecord.org == org)).all()
            return [
                (row.id, _record(row)) for row in rows if self._ensure_aware(row.expires_at) >= now
            ]

    def sessions_for_identities(
        self, identities: Iterable[str]
    ) -> list[tuple[str, SessionIdentity]]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        wanted = list(set(identities))
        if not wanted:
            return []
        now = self._now()
        with Session(self._engine) as session:
            rows = session.scalars(
                select(SessionRecord).where(SessionRecord.identity.in_(wanted))
            ).all()
            return [
                (row.id, _record(row)) for row in rows if self._ensure_aware(row.expires_at) >= now
            ]

    def revoke(self, sids: Iterable[str]) -> int:
        from sqlalchemy import delete
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        wanted = list(set(sids))
        if not wanted:
            return 0
        with Session(self._engine) as session:
            result = session.execute(delete(SessionRecord).where(SessionRecord.id.in_(wanted)))
            session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    def revoke_identities(self, identities: Iterable[str]) -> int:
        from sqlalchemy import delete
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        wanted = list(set(identities))
        if not wanted:
            return 0
        # Rows are removed rather than expired in place: a revoked session must not come back if a
        # clock moves, and `valid`/`identity` both read the row before checking its expiry.
        with Session(self._engine) as session:
            result = session.execute(
                delete(SessionRecord).where(SessionRecord.identity.in_(wanted))
            )
            session.commit()
            return int(getattr(result, "rowcount", 0) or 0)


def _record(row: Any) -> SessionIdentity:
    """A `sessions` row as the store's own record type. A column left NULL by an older row reads as
    "nothing observed, nothing selected"."""
    return SessionIdentity(
        login=row.identity,
        github_orgs=tuple(row.github_orgs or ()),
        teams=tuple(row.teams or ()),
        org=row.org,
        role=row.role,
    )

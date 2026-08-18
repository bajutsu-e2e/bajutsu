"""Server SessionStore implementations for the hosted backend.

`RedisSessionStore` (BE-0015 7b, legacy) keeps sessions in Redis; `SqlSessionStore` (BE-0106) keeps
them in the same Postgres the system of record already uses, so Redis is no longer needed. Both
survive a control-plane restart and span replicas. Clients are **injected**, so the module imports
neither redis nor SQLAlchemy at the top — safe to import and unit-test without the optional extras;
the real client/engine is wired in by the server selection."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_SESSION = "bajutsu:session:"  # Redis key prefix for a login-session id
_DEFAULT_TTL = 604800  # seconds a session lives before Redis evicts it (7 days)


class RedisLike(Protocol):
    """The slice of a redis-py client `RedisSessionStore` uses (so a fake can stand in)."""

    def setex(self, key: str, seconds: int, value: str) -> object:
        """Set *key* to *value* with a *seconds* time-to-live."""

    def exists(self, key: str) -> object:
        """Return a truthy count when *key* exists."""

    def get(self, key: str) -> object:
        """Return *key*'s value (bytes), or None if unset."""

    def scan_iter(self, match: str) -> Iterable[object]:
        """Iterate the keys matching *match* (a glob), as redis-py's own `scan_iter` does."""

    def delete(self, *keys: str) -> object:
        """Delete the named keys, returning how many existed."""


class RedisSessionStore:
    """SessionStore backed by Redis keys with a TTL, so finished sessions self-expire. The key's
    value carries the session's identity (or an empty string when it has none)."""

    def __init__(self, redis: RedisLike, *, ttl: int = _DEFAULT_TTL) -> None:
        self._redis = redis
        self._ttl = ttl

    def issue(self, identity: str | None = None) -> str:
        sid = secrets.token_urlsafe(32)
        self._redis.setex(f"{_SESSION}{sid}", self._ttl, identity or "")
        return sid

    def valid(self, sid: str) -> bool:
        return bool(self._redis.exists(f"{_SESSION}{sid}"))

    def identity(self, sid: str) -> str | None:
        raw = self._redis.get(f"{_SESSION}{sid}")
        if raw is None:
            return None
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        return value or None

    def revoke_identities(self, identities: Iterable[str]) -> int:
        wanted = set(identities)
        if not wanted:
            return 0
        # The identity is the key's *value*, so there is no index to look it up by — every live
        # session key has to be read. Acceptable because revocation is a rare admin action (retiring
        # an org), and the alternative is leaving this store unable to revoke at all, which is the
        # hole BE-0375 closed for the two stores a deployment actually runs.
        doomed = []
        for key in self._redis.scan_iter(f"{_SESSION}*"):
            name = key.decode() if isinstance(key, bytes) else str(key)
            raw = self._redis.get(name)
            if raw is None:
                continue
            who = raw.decode() if isinstance(raw, bytes) else str(raw)
            if who in wanted:
                doomed.append(name)
        if doomed:
            self._redis.delete(*doomed)
        return len(doomed)


class SqlSessionStore:
    """SessionStore backed by a Postgres (or SQLite) sessions table (BE-0106).

    Replaces `RedisSessionStore`: sessions survive a restart and span replicas exactly as the Redis
    store did, with no second stateful service. Expiry is enforced on read; the engine is injected
    so a test can hand in an in-memory SQLite."""

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

    def issue(self, identity: str | None = None) -> str:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        sid = secrets.token_urlsafe(32)
        expires = self._now() + timedelta(seconds=self._ttl)
        with Session(self._engine) as session:
            session.add(SessionRecord(id=sid, identity=identity, expires_at=expires))
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
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import SessionRecord

        with Session(self._engine) as session:
            row = session.get(SessionRecord, sid)
            if row is None or self._ensure_aware(row.expires_at) < self._now():
                return None
            return row.identity

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

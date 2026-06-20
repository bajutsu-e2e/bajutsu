"""A Redis-backed SessionStore for the hosted backend (BE-0015 7b-1).

`InMemorySessionStore` keeps sessions in one process, so a restart drops them. `RedisSessionStore`
keeps each opaque session id as a Redis key with a TTL, so sessions survive a control-plane restart
and are visible to every replica. The redis client is **injected** (the `RedisLike` slice below), so
this module imports no redis — safe to import and unit-test without the ``worker`` extra; the real
client is wired in by the server selection (the same client that backs the log bus)."""

from __future__ import annotations

import secrets
from typing import Protocol

_SESSION = "bajutsu:session:"  # Redis key prefix for a login-session id
_DEFAULT_TTL = 604800  # seconds a session lives before Redis evicts it (7 days)


class RedisLike(Protocol):
    """The slice of a redis-py client `RedisSessionStore` uses (so a fake can stand in)."""

    def setex(self, key: str, seconds: int, value: str) -> object:
        """Set *key* to *value* with a *seconds* time-to-live."""

    def exists(self, key: str) -> object:
        """Return a truthy count when *key* exists."""


class RedisSessionStore:
    """SessionStore backed by Redis keys with a TTL, so finished sessions self-expire."""

    def __init__(self, redis: RedisLike, *, ttl: int = _DEFAULT_TTL) -> None:
        self._redis = redis
        self._ttl = ttl

    def issue(self) -> str:
        sid = secrets.token_urlsafe(32)
        self._redis.setex(f"{_SESSION}{sid}", self._ttl, "1")
        return sid

    def valid(self, sid: str) -> bool:
        return bool(self._redis.exists(f"{_SESSION}{sid}"))

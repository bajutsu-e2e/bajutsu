"""Tests for the SessionStore seam (BE-0015 7b-1, BE-0106).

`InMemorySessionStore` is the local default — sessions live in one process, so a restart drops them.
`RedisSessionStore` is the legacy server implementation (kept for reference); `SqlSessionStore` is its
replacement (BE-0106): sessions in the same Postgres the system of record already uses, so no Redis
is needed. Both server stores survive a restart and span replicas. The redis client / SQL engine are
injected, so in-memory fakes (a dict for Redis, SQLite for SQL) drive the contract — no live
Redis or Postgres on the gate. The `SqlSessionStore` cases run against in-memory SQLite in the gate
and, behind the `postgres` marker, against a real Postgres service in the serve-db.yml lane
(BE-0309)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import Engine

from bajutsu.serve.server.models import Base
from bajutsu.serve.server.sessions import _DEFAULT_TTL, RedisSessionStore, SqlSessionStore
from bajutsu.serve.sessions import InMemorySessionStore


def test_in_memory_issue_then_valid() -> None:
    store = InMemorySessionStore()
    sid = store.issue()
    assert store.valid(sid)


def test_in_memory_unknown_is_invalid() -> None:
    assert not InMemorySessionStore().valid("nope")


def test_in_memory_binds_and_reads_identity() -> None:
    store = InMemorySessionStore()
    sid = store.issue("alice")
    assert store.identity(sid) == "alice"
    # a token login carries no identity; an unknown id has none either
    assert store.identity(store.issue()) is None
    assert store.identity("nope") is None


def test_in_memory_ids_are_unique_and_opaque() -> None:
    store = InMemorySessionStore()
    a, b = store.issue(), store.issue()
    assert a != b
    assert len(a) > 20  # secrets.token_urlsafe(32) is not a short, guessable id


class FakeRedis:
    """The slice of a redis client RedisSessionStore uses, in memory. Records TTLs so a test can
    assert each session key self-expires."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key: str, seconds: int, value: str) -> object:
        self._kv[key] = value
        self.ttls[key] = seconds
        return True

    def exists(self, key: str) -> int:
        return 1 if key in self._kv else 0

    def get(self, key: str) -> bytes | None:
        v = self._kv.get(key)
        return v.encode() if v is not None else None


def test_redis_issue_then_valid() -> None:
    store = RedisSessionStore(FakeRedis())
    sid = store.issue()
    assert store.valid(sid)


def test_redis_unknown_is_invalid() -> None:
    assert not RedisSessionStore(FakeRedis()).valid("nope")


def test_redis_binds_and_reads_identity() -> None:
    store = RedisSessionStore(FakeRedis())
    assert store.identity(store.issue("bob")) == "bob"
    # a token login carries no identity; an unknown id has none
    assert store.identity(store.issue()) is None
    assert store.identity("nope") is None


def test_redis_issue_sets_the_injected_ttl() -> None:
    redis = FakeRedis()
    RedisSessionStore(redis, ttl=123).issue()
    assert list(redis.ttls.values()) == [123]


def test_redis_issue_uses_the_default_ttl() -> None:
    redis = FakeRedis()
    RedisSessionStore(redis).issue()
    assert list(redis.ttls.values()) == [_DEFAULT_TTL]


def test_session_ttl_from_env_parses_and_validates() -> None:
    from bajutsu.serve import _session_ttl_from_env

    assert _session_ttl_from_env(None, 99) == 99  # unset -> default
    assert _session_ttl_from_env("", 99) == 99  # empty -> default
    assert _session_ttl_from_env("3600", 99) == 3600
    for bad in ("7d", "abc", "1.5"):
        with pytest.raises(ValueError, match="BAJUTSU_SESSION_TTL"):
            _session_ttl_from_env(bad, 99)
    for nonpos in ("0", "-5"):
        with pytest.raises(ValueError, match="positive"):
            _session_ttl_from_env(nonpos, 99)


# ---------------------------------------------------------------------------
# SqlSessionStore (BE-0106) — sessions in Postgres (SQLite on the gate)
# ---------------------------------------------------------------------------


def _sql_store(serve_engine: Callable[..., Engine], ttl: int = 3600) -> SqlSessionStore:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    return SqlSessionStore(engine, ttl=ttl)


def test_sql_issue_then_valid(serve_engine: Callable[..., Engine]) -> None:
    store = _sql_store(serve_engine)
    sid = store.issue()
    assert store.valid(sid)


def test_sql_unknown_is_invalid(serve_engine: Callable[..., Engine]) -> None:
    assert not _sql_store(serve_engine).valid("nope")


def test_sql_binds_and_reads_identity(serve_engine: Callable[..., Engine]) -> None:
    store = _sql_store(serve_engine)
    assert store.identity(store.issue("carol")) == "carol"
    assert store.identity(store.issue()) is None
    assert store.identity("nope") is None


def test_sql_ids_are_unique_and_opaque(serve_engine: Callable[..., Engine]) -> None:
    store = _sql_store(serve_engine)
    a, b = store.issue(), store.issue()
    assert a != b
    assert len(a) > 20


def test_sql_expired_session_is_invalid(serve_engine: Callable[..., Engine]) -> None:
    store = _sql_store(serve_engine, ttl=-1)
    sid = store.issue()
    assert not store.valid(sid)
    assert store.identity(sid) is None

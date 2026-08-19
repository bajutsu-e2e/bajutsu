"""Tests for the SessionStore seam (BE-0015 7b-1, BE-0106).

`InMemorySessionStore` is the local default — sessions live in one process, so a restart drops them.
`SqlSessionStore` (BE-0106) keeps them in the same Postgres the system of record already uses, so
they survive a restart and span replicas. The SQL engine is injected, so SQLite drives the contract —
no live Postgres on the gate. The `SqlSessionStore` cases run against in-memory SQLite in the gate
and, behind the `postgres` marker, against a real Postgres service in the serve-db.yml lane
(BE-0309)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import Engine

from bajutsu.serve.server.models import Base
from bajutsu.serve.server.sessions import SqlSessionStore
from bajutsu.serve.sessions import InMemorySessionStore, SessionIdentity


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


# Revocation (BE-0375, session-scoped org selection) — retiring an org, or narrowing its membership,
# has to reach the sessions already held as it. Every store implements the lookups, since which one
# is wired is a deployment choice the operation cannot see: a hole in any of them would be a retired
# tenant still acting through a live cookie.


def test_in_memory_finds_sessions_by_identity() -> None:
    store = InMemorySessionStore()
    bob, _alice = store.issue(identity="bob"), store.issue(identity="alice")
    store.issue()  # a shared-token login carries no identity, so it belongs to no org
    assert [sid for sid, _ in store.sessions_for_identities(["bob", "never-signed-in"])] == [bob]
    assert store.sessions_for_identities([]) == []


def test_in_memory_revoke_of_nothing_is_a_no_op() -> None:
    # Retiring an org with no recorded user must not walk the whole session map, nor report a drop.
    store = InMemorySessionStore()
    live = store.issue(identity="bob")
    assert store.revoke([]) == 0
    assert store.valid(live)


def test_sql_finds_sessions_by_identity(serve_engine: Callable[..., Engine]) -> None:
    store = _sql_store(serve_engine)
    bob, alice = store.issue(identity="bob"), store.issue(identity="alice")
    store.issue()
    assert [sid for sid, _ in store.sessions_for_identities(["bob"])] == [bob]
    assert store.valid(alice)
    assert store.sessions_for_identities([]) == []


def test_sql_revoke_removes_the_row_rather_than_expiring_it(
    serve_engine: Callable[..., Engine],
) -> None:
    # Removed, not expired in place: `valid` and `identity` both read the row before checking its
    # expiry, so a revoked session must not be able to come back if a clock moves.
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import SessionRecord

    store = _sql_store(serve_engine)
    sid = store.issue(identity="bob")
    assert store.revoke([sid]) == 1
    with Session(store._engine) as session:
        assert list(session.scalars(select(SessionRecord))) == []


# --- the org a session acts as (session-scoped org selection) ---------------------------------


def _selected(store: object, sid: str) -> tuple[str | None, str | None]:
    record = store.context(sid)  # type: ignore[attr-defined]
    return (record.org, record.role) if record is not None else (None, None)


def test_in_memory_carries_the_sign_ins_facts_and_selection() -> None:
    store = InMemorySessionStore()
    sid = store.issue(
        "alice",
        context=SessionIdentity(
            login="alice",
            github_orgs=("acme-gh",),
            teams=("acme-gh/maintainers",),
            org="acme",
            role="editor",
        ),
    )
    record = store.context(sid)
    assert record is not None
    assert record.github_orgs == ("acme-gh",) and record.teams == ("acme-gh/maintainers",)
    assert (record.org, record.role) == ("acme", "editor")


def test_a_context_naming_another_login_is_dropped() -> None:
    # The facts a session acts on are the ones its own sign-in saw, so a mismatched record is not
    # stored — the session simply carries its login and nothing else.
    store = InMemorySessionStore()
    sid = store.issue("alice", context=SessionIdentity(login="mallory", org="globex"))
    record = store.context(sid)
    assert record is not None
    assert record.login == "alice" and record.org is None and record.github_orgs == ()


def test_in_memory_select_org_moves_only_the_named_session() -> None:
    store = InMemorySessionStore()
    first = store.issue("alice", context=SessionIdentity(login="alice", org="acme", role="viewer"))
    second = store.issue("alice", context=SessionIdentity(login="alice", org="acme", role="viewer"))
    assert store.select_org(first, "globex", "editor")
    assert _selected(store, first) == ("globex", "editor")
    assert _selected(store, second) == ("acme", "viewer")  # the other window keeps its tenant
    assert not store.select_org("nope", "globex", "editor")


def test_in_memory_lists_and_revokes_by_selected_org() -> None:
    store = InMemorySessionStore()
    acme = store.issue("alice", context=SessionIdentity(login="alice", org="acme", role="viewer"))
    globex = store.issue("bob", context=SessionIdentity(login="bob", org="globex", role="viewer"))
    assert [sid for sid, _ in store.sessions_for_org("acme")] == [acme]
    assert store.revoke([acme]) == 1
    assert not store.valid(acme) and store.valid(globex)


def test_sql_carries_the_selection_across_a_new_store(
    serve_engine: Callable[..., Engine],
) -> None:
    # The point of the database-backed store: a switch outlives the process that served it.
    engine = serve_engine()
    Base.metadata.create_all(engine)
    sid = SqlSessionStore(engine).issue(
        "alice",
        context=SessionIdentity(login="alice", github_orgs=("acme-gh",), org="acme", role="viewer"),
    )
    assert SqlSessionStore(engine).select_org(sid, "globex", "editor")
    record = SqlSessionStore(engine).context(sid)
    assert record is not None
    assert (record.org, record.role) == ("globex", "editor")
    assert record.github_orgs == ("acme-gh",)


def test_sql_lists_and_revokes_by_selected_org(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    store = SqlSessionStore(engine)
    acme = store.issue("alice", context=SessionIdentity(login="alice", org="acme"))
    globex = store.issue("bob", context=SessionIdentity(login="bob", org="globex"))
    assert [sid for sid, _ in store.sessions_for_org("globex")] == [globex]
    assert store.revoke([globex]) == 1
    assert not store.valid(globex) and store.valid(acme)


def test_sql_reads_a_row_written_before_the_selection_existed(
    serve_engine: Callable[..., Engine],
) -> None:
    # The migration adds four nullable columns, so a session issued before it upgrades in place and
    # reads as "nothing observed, nothing selected" — the pre-selection behavior.
    engine = serve_engine()
    Base.metadata.create_all(engine)
    store = SqlSessionStore(engine)
    sid = store.issue("alice")
    record = store.context(sid)
    assert record is not None
    assert record.login == "alice"
    assert record.github_orgs == () and record.teams == ()
    assert record.org is None and record.role is None

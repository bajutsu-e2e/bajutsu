"""The SessionStore seam: opaque login-session ids (BE-0015 7b).

`issue()` mints a fresh id (set as the `bajutsu_session` cookie at login); `valid()` checks one.
A session may carry an *identity* (the GitHub login from an OAuth login, BE-0015 7b-2) so a later
layer (RBAC, 7c) can map a session back to a user; a shared-token login carries none. Alongside the
login, an OAuth session records what its sign-in observed on GitHub and which org it currently acts
as, so a login that belongs to several orgs can switch between them without signing out
(session-scoped org selection).
`InMemorySessionStore` is the local default — sessions live in this process, so a restart drops them
(re-login). The server backend swaps in a database-backed store (`SqlSessionStore`) so sessions
survive restarts and span control-plane processes; the seam keeps `ServeState` and the auth layer
unaware of which is in use."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionIdentity:
    """Everything a session records about the person holding it (session-scoped org selection).

    `login` is the GitHub login (None for a shared-token session, which records nothing else).
    `github_orgs` and `teams` are what the sign-in observed on GitHub, kept because the orgs this
    session may act as — and the role each of them grants — are derived from them long after the
    OAuth callback that saw them. `org` is the org the session currently acts as and `role` the role
    that org grants; both are None on a session issued before a selection existed, which reads as
    "no selection" and falls back to the user row.
    """

    login: str | None = None
    github_orgs: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    org: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class Caller:
    """Who one request is, resolved once at the authentication gate.

    The org and the role travel with the login rather than being looked up per operation, so a
    request acts as the org its own session selected — and is authorized with that org's role —
    instead of the single org the user row records. `org` is None when the session recorded no
    selection, which sends `ServeState.org_of` back to the user row; `role` is None on the same
    session, which sends the role gate back to the stored one.
    """

    login: str
    org: str | None = None
    role: str | None = None


@runtime_checkable
class SessionStore(Protocol):
    """Issues and validates opaque login-session ids, optionally bound to an identity."""

    def issue(self, identity: str | None = None, *, context: SessionIdentity | None = None) -> str:
        """Mint and remember a new opaque session id, optionally bound to *identity*.

        *context* carries what the sign-in observed on GitHub and the org the session starts as; it
        is ignored unless it names the same login, so a caller cannot bind one login's session to
        another's GitHub facts.
        """

    def valid(self, sid: str) -> bool:
        """Whether *sid* is a known, live session."""

    def identity(self, sid: str) -> str | None:
        """The identity bound to *sid* (e.g. a GitHub login), or None if it has none / is unknown."""

    def context(self, sid: str) -> SessionIdentity | None:
        """Everything *sid* records, or None if it is unknown or expired.

        The gate reads the whole record in one call rather than the login alone, because the org and
        the role it resolves for the request come from the same row.
        """

    def select_org(self, sid: str, org: str, role: str) -> bool:
        """Point *sid* at *org* with *role*, returning whether the session existed.

        The role is stored beside the org because a role is granted per org (`editorTeam`,
        BE-0313): keeping the two apart would let a request act as one org with another's role.
        """

    def sessions_for_org(self, org: str) -> list[tuple[str, SessionIdentity]]:
        """Every live session that currently acts as *org*, as `(sid, record)` pairs.

        Membership edits and org deletion both have to reach the sessions a selection put in an org,
        which no query over the users table can find (session-scoped org selection).
        """

    def sessions_for_identities(
        self, identities: Iterable[str]
    ) -> list[tuple[str, SessionIdentity]]:
        """Every live session bound to one of *identities*, as `(sid, record)` pairs.

        Retiring an org has to reach the sessions its members already hold (BE-0375): a soft delete
        turns away their *next* sign-in, but a cookie issued before it keeps acting as that tenant
        until it expires. The ones this finds are the sessions that recorded no selection, whose org
        is still their user row's; a session of the same login acting as another org is left to the
        caller to filter out. Sessions carrying no identity (a shared-token login) never match — they
        belong to no org.
        """

    def revoke(self, sids: Iterable[str]) -> int:
        """Drop the named sessions; returns how many were live."""


@dataclass
class InMemorySessionStore:
    """Sessions in a process-local map (the pre-7b behavior) — a restart drops them, so the user
    simply logs in again. Maps each id to what it records (a bare login for a shared-token
    session)."""

    _sessions: dict[str, SessionIdentity] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def issue(self, identity: str | None = None, *, context: SessionIdentity | None = None) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[sid] = _record_for(identity, context)
        return sid

    def valid(self, sid: str) -> bool:
        with self._lock:
            return sid in self._sessions

    def identity(self, sid: str) -> str | None:
        with self._lock:
            record = self._sessions.get(sid)
        return record.login if record is not None else None

    def context(self, sid: str) -> SessionIdentity | None:
        with self._lock:
            return self._sessions.get(sid)

    def select_org(self, sid: str, org: str, role: str) -> bool:
        with self._lock:
            record = self._sessions.get(sid)
            if record is None:
                return False
            self._sessions[sid] = replace(record, org=org, role=role)
        return True

    def sessions_for_org(self, org: str) -> list[tuple[str, SessionIdentity]]:
        with self._lock:
            return [(sid, rec) for sid, rec in self._sessions.items() if rec.org == org]

    def sessions_for_identities(
        self, identities: Iterable[str]
    ) -> list[tuple[str, SessionIdentity]]:
        wanted = set(identities)
        with self._lock:
            return [(sid, rec) for sid, rec in self._sessions.items() if rec.login in wanted]

    def revoke(self, sids: Iterable[str]) -> int:
        with self._lock:
            doomed = [sid for sid in set(sids) if sid in self._sessions]
            for sid in doomed:
                del self._sessions[sid]
        return len(doomed)


def _record_for(identity: str | None, context: SessionIdentity | None) -> SessionIdentity:
    """What a freshly issued session records. A *context* naming another login is dropped rather
    than trusted, so the GitHub facts a session acts on are always the ones its own sign-in saw."""
    if context is not None and context.login == identity:
        return context
    return SessionIdentity(login=identity)


def login_of(caller: Caller | None) -> str | None:
    """The login *caller* attributes an action to, or None for a session that carries no identity.

    Attribution stays a login rather than the whole caller: an audit row, a job, and an upload all
    record who acted, not which org the acting session had selected at the time.
    """
    return caller.login if caller is not None else None

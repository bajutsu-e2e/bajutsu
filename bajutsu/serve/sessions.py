"""The SessionStore seam: opaque login-session ids (BE-0015 7b).

`issue()` mints a fresh id (set as the `bajutsu_session` cookie at login); `valid()` checks one.
A session may carry an *identity* (the GitHub login from an OAuth login, BE-0015 7b-2) so a later
layer (RBAC, 7c) can map a session back to a user; a shared-token login carries none.
`InMemorySessionStore` is the local default — sessions live in this process, so a restart drops them
(re-login). The server backend swaps in a database-backed store (`SqlSessionStore`) so sessions
survive restarts and span control-plane processes; the seam keeps `ServeState` and the auth layer
unaware of which is in use."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Iterable
from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Issues and validates opaque login-session ids, optionally bound to an identity."""

    def issue(self, identity: str | None = None) -> str:
        """Mint and remember a new opaque session id, optionally bound to *identity*."""

    def valid(self, sid: str) -> bool:
        """Whether *sid* is a known, live session."""

    def identity(self, sid: str) -> str | None:
        """The identity bound to *sid* (e.g. a GitHub login), or None if it has none / is unknown."""

    def revoke_identities(self, identities: Iterable[str]) -> int:
        """Drop every live session bound to one of *identities*; returns how many were dropped.

        Retiring an org has to reach the sessions its members already hold (BE-0375): a soft delete
        turns away their *next* sign-in, but a cookie issued before it keeps acting as that tenant
        until it expires. Sessions carrying no identity (a shared-token login) are never touched —
        they belong to no org.
        """


class InMemorySessionStore:
    """Sessions in a process-local map (the pre-7b behavior) — a restart drops them, so the user
    simply logs in again. Maps each id to its identity (None for a shared-token login)."""

    def __init__(self) -> None:
        self._sessions: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def issue(self, identity: str | None = None) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[sid] = identity
        return sid

    def valid(self, sid: str) -> bool:
        with self._lock:
            return sid in self._sessions

    def identity(self, sid: str) -> str | None:
        with self._lock:
            return self._sessions.get(sid)

    def revoke_identities(self, identities: Iterable[str]) -> int:
        wanted = set(identities)
        if not wanted:
            return 0
        with self._lock:
            doomed = [sid for sid, who in self._sessions.items() if who in wanted]
            for sid in doomed:
                del self._sessions[sid]
        return len(doomed)

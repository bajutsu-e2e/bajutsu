"""The SessionStore seam: opaque login-session ids (BE-0015 7b-1).

`issue()` mints a fresh id (set as the `bajutsu_session` cookie at login); `valid()` checks one.
`InMemorySessionStore` is the local default — sessions live in this process, so a restart drops them
(re-login). The server backend swaps in a Redis-backed store so sessions survive restarts and span
control-plane processes; the seam keeps `ServeState` and the auth layer unaware of which is in use."""

from __future__ import annotations

import secrets
import threading
from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Issues and validates opaque login-session ids."""

    def issue(self) -> str:
        """Mint and remember a new opaque session id."""

    def valid(self, sid: str) -> bool:
        """Whether *sid* is a known, live session."""


class InMemorySessionStore:
    """Sessions in a process-local set (the pre-7b behavior) — a restart drops them, so the user
    simply logs in again."""

    def __init__(self) -> None:
        self._sessions: set[str] = set()
        self._lock = threading.Lock()

    def issue(self) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions.add(sid)
        return sid

    def valid(self, sid: str) -> bool:
        with self._lock:
            return sid in self._sessions

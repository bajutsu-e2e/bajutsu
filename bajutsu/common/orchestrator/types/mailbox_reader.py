"""The inbox seam the `email` step reads through (BE-0046, BE-0514)."""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol

from bajutsu.common.drivers import base
from bajutsu.common.mailbox import MailboxMessage


class MailboxTimeout(base.SelectorError):
    """A reader call ran out of its `timeout` before the server answered."""


class MailboxReader(Protocol):
    """Reads the inbox for the `email` step. Injected by the runner, built from
    `targets.<name>.services.mailbox`; None means no mailbox is configured (or the fake driver), in
    which case an `email` step fails cleanly. Both methods may raise `base.SelectorError` on a
    connection, login, or certificate failure — a clean step failure, never a silent wrong value.

    Listing and fetching are split so a poll downloads each message once rather than the whole
    mailbox every time. `timeout` (seconds) bounds a single call, so a slow server can't overrun the
    step's own `email.timeout`; the handler passes the time remaining in the poll."""

    def list_ids(self, timeout: float) -> frozenset[str]: ...

    def fetch(self, ids: Collection[str], timeout: float) -> list[MailboxMessage]: ...

    def check_same_inbox(self, baseline: frozenset[str], current: frozenset[str]) -> None:
        """Raise `base.SelectorError` when `current`'s ids can't be compared with `baseline`'s —
        a rebuilt inbox renames old mail, which would then pass for mail that just arrived."""
        ...

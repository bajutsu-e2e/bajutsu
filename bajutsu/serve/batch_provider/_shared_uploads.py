"""Upload ARNs a batch worker keeps by key while a job holding the key is in flight (BE-0484)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from bajutsu.common.cloud.devicefarm import DeviceFarmError

# What a maker returns: the upload's ARN, and the names the uploaded archive holds.
Made = tuple[str, frozenset[str]]


class _Entry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.holders = 0
        self.arn: str | None = None
        self.members: frozenset[str] = frozenset()
        self.error: Exception | None = None


class SharedUploads:
    """Upload ARNs kept by key, each while at least one job holds its key.

    The first holder to need an upload makes it, and the others wait on the key's lock. A failed
    upload fails every holder that waited on it and needs more than the kept upload holds. The key
    then starts afresh for a later holder. Once no job holds the key, the entry drops, and a later job uploads
    again.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[Callable[..., tuple[str, bool]]]:
        """Hold *key* for the block; yields `get(make, needs=None)` -> ``(arn, reused)``.

        `get` returns the kept ARN when there is one and its archive holds *needs*; otherwise it calls
        *make* and keeps what it returns.
        """
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            entry.holders += 1

        def get(make: Callable[[], Made], needs: str | None = None) -> tuple[str, bool]:
            with entry.lock:
                # A failed rebuild leaves the earlier upload intact, which still serves its members.
                if entry.arn is not None and (needs is None or needs in entry.members):
                    return entry.arn, True
                if entry.error is not None:
                    raise DeviceFarmError(
                        f"the shared upload for {key} failed: {entry.error}"
                    ) from entry.error
                try:
                    entry.arn, entry.members = make()
                except Exception as exc:
                    entry.error = exc
                    self._forget(key, entry)
                    raise
                return entry.arn, False

        try:
            yield get
        finally:
            with self._lock:
                entry.holders -= 1
                if entry.holders == 0:
                    self._forget_locked(key, entry)

    def _forget(self, key: str, entry: _Entry) -> None:
        with self._lock:
            self._forget_locked(key, entry)

    def _forget_locked(self, key: str, entry: _Entry) -> None:
        if self._entries.get(key) is entry:
            del self._entries[key]

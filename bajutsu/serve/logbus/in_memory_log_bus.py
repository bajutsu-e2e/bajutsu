"""The in-memory log bus, buffering each job's lines — serve's default."""

from __future__ import annotations

import threading
from collections.abc import Iterator

from ._channel import _Channel


class InMemoryLogBus:
    """Buffers each job's lines in memory — the default for `bajutsu serve`.

    The buffer (not a fire-and-forget pub/sub) is what lets a late subscriber replay the whole
    log; the server backend's `PostCompletionLogBus` gets the same property by reading the
    worker's uploaded console.log after the job completes.
    """

    def __init__(self) -> None:
        self._chans: dict[str, _Channel] = {}
        self._lock = threading.Lock()

    def _chan(self, job_id: str) -> _Channel:
        with self._lock:
            return self._chans.setdefault(job_id, _Channel())

    def publish(self, job_id: str, line: str) -> None:
        ch = self._chan(job_id)
        with ch.cond:
            ch.lines.append(line)
            ch.cond.notify_all()

    def publish_many(self, job_id: str, lines: list[str]) -> None:
        ch = self._chan(job_id)
        with ch.cond:
            ch.lines.extend(lines)
            ch.cond.notify_all()

    def discard(self, job_id: str) -> None:
        """Forget *job_id*'s buffered lines, so a finished job's log does not stay in memory."""
        with self._lock:
            self._chans.pop(job_id, None)

    def close(self, job_id: str, final: str | None = None) -> None:
        ch = self._chan(job_id)
        with ch.cond:
            ch.closed = True
            if final is not None:
                ch.final = final
            ch.cond.notify_all()

    def final(self, job_id: str) -> str | None:
        with self._lock:
            ch = self._chans.get(job_id)
        if ch is None:
            return None
        with ch.cond:  # `close` writes ch.final under this lock — read it the same way
            return ch.final

    def stream(self, job_id: str, *, timeout: float | None = None) -> Iterator[str | None]:
        ch = self._chan(job_id)
        i = 0
        while True:
            with ch.cond:
                if i >= len(ch.lines) and not ch.closed:
                    ch.cond.wait(timeout)  # wakes on a new line, close, or (if set) the timeout
                batch = ch.lines[i:]
                i = len(ch.lines)
                done = ch.closed
            if batch:
                yield from batch  # yield outside the lock so a slow consumer can't block producers
            elif done:
                return
            elif timeout is not None:
                yield None  # waited `timeout` with no new line and not closed → heartbeat
            # else (no timeout): a spurious wakeup with no lines — loop and block again

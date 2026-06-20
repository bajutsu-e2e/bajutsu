"""The LogBus seam: how a job's live log reaches a subscriber (BE-0015 local/server parity).

A run/record/crawl job streams output line by line. `LogBus` is the one point where delivery
diverges between local and server hosting: locally lines are buffered in process
(`InMemoryLogBus`), while a server backend would publish them to a Redis stream consumed by other
replicas. A subscriber gets the backlog already published plus any live lines, and the stream
ends once the job is `close`d — so a subscriber that attaches after the job finished still
replays everything.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


class LogBus(Protocol):
    """Carries a job's log lines from producer to live subscribers."""

    def publish(self, job_id: str, line: str) -> None: ...

    def close(self, job_id: str) -> None:
        """Signal that no more lines will be published for *job_id* (the job finished)."""
        ...

    def stream(self, job_id: str) -> Iterator[str]:
        """Yield the buffered backlog then any live lines, ending once the job is closed."""
        ...


@dataclass
class _Channel:
    lines: list[str] = field(default_factory=list)
    closed: bool = False
    cond: threading.Condition = field(default_factory=threading.Condition)


class InMemoryLogBus:
    """Buffers each job's lines in memory — the default for `bajutsu serve`.

    The buffer (not a fire-and-forget pub/sub) is what lets a late subscriber replay the whole
    log; a Redis-backed bus gets the same property from a persisted stream.
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

    def close(self, job_id: str) -> None:
        ch = self._chan(job_id)
        with ch.cond:
            ch.closed = True
            ch.cond.notify_all()

    def stream(self, job_id: str) -> Iterator[str]:
        ch = self._chan(job_id)
        i = 0
        while True:
            with ch.cond:
                while i >= len(ch.lines) and not ch.closed:
                    ch.cond.wait()
                batch = ch.lines[i:]
                i = len(ch.lines)
                done = ch.closed
            yield from batch  # yield outside the lock so a slow consumer can't block producers
            if done and not batch:
                return

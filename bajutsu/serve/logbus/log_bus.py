"""The seam a job's log lines travel from producer to live subscribers through."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol


class LogBus(Protocol):
    """Carries a job's log lines from producer to live subscribers."""

    def publish(self, job_id: str, line: str) -> None:
        """Append one log *line* to *job_id*'s channel."""

    def publish_many(self, job_id: str, lines: list[str]) -> None:
        """Append *lines* to *job_id*'s channel in order, as one write where the store allows."""

    def close(self, job_id: str, final: str | None = None) -> None:
        """Signal that no more lines will be published for *job_id* (the job finished), optionally
        recording its terminal status payload (a JSON view) for `final` to return."""

    def stream(self, job_id: str, *, timeout: float | None = None) -> Iterator[str | None]:
        """Yield the buffered backlog then any live lines, ending once the job is closed. With
        *timeout*, yield ``None`` as a heartbeat when no new line arrives within that many seconds
        and the job isn't closed — so a caller can emit a keepalive and check for a disconnect;
        without it, block until the next line or close (never yielding ``None``)."""

    def final(self, job_id: str) -> str | None:
        """The terminal status payload recorded at `close`, or None if the job hasn't finished (or
        finished without one). Lets a control-plane replica read a worker-run job's result."""

"""A post-completion LogBus for the server backend (BE-0106).

In the post-completion model the worker collects all output and uploads it as
``runs/<id>/console.log`` after the run finishes.  This LogBus bridges the existing ``/events``
SSE contract — a stream that a late subscriber can open, that yields periodic heartbeats while
the job is in flight, and that ends with the full log once the job completes.

``publish`` and ``close`` are no-ops: the worker never calls them (it writes to the object store
instead).  ``stream`` polls the jobs table for status; ``final`` reads the terminal result from
the jobs table.  The repository and artifact store are **injected**, so no SQLAlchemy or boto3 is
imported at the top."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from bajutsu.serve.artifacts import ArtifactStore
    from bajutsu.serve.server.db import Repository


class PostCompletionLogBus:
    """LogBus backed by the jobs table + object-store console.log (BE-0106).

    The worker uploads the run's full stdout as ``runs/<id>/console.log``; this bus serves it
    to the ``/events`` SSE endpoint after the job completes.  While the job is in flight, the
    stream yields periodic heartbeats so the connection stays alive.

    ``artifacts_fn`` is a callable that returns an `ArtifactStore` for a given org id, so the
    bus reads from the correct org-scoped prefix."""

    def __init__(
        self,
        repository: Repository,
        artifacts_fn: Callable[[str], ArtifactStore | None] | None = None,
        *,
        poll_interval: float = 2.0,
    ) -> None:
        self._repo = repository
        self._artifacts_fn = artifacts_fn
        self._poll = poll_interval
        self._finals: dict[str, str] = {}

    def publish(self, job_id: str, line: str) -> None:
        _ = job_id, line

    def close(self, job_id: str, final: str | None = None) -> None:
        if final is not None:
            self._finals[job_id] = final

    def final(self, job_id: str) -> str | None:
        if job_id in self._finals:
            return self._finals[job_id]
        info = self._repo.get_job(job_id)
        if info is None:
            return None
        if info["status"] in ("done", "failed"):
            result: dict[str, Any] = info["result"]
            import json

            payload = json.dumps(result)
            self._finals[job_id] = payload
            return payload
        return None

    def stream(self, job_id: str, *, timeout: float | None = None) -> Iterator[str | None]:
        while True:
            info = self._repo.get_job(job_id)
            if info is not None and info["status"] in ("done", "failed"):
                yield from self._read_console_log(info.get("org_id", ""), job_id)
                return
            time.sleep(self._poll)
            if timeout is not None:
                yield None  # heartbeat only when timeout is set (LogBus contract)

    def _read_console_log(self, org_id: str, job_id: str) -> list[str]:
        if self._artifacts_fn is None:
            return []
        store = self._artifacts_fn(org_id)
        if store is None:
            return []
        content = store.open_bytes(f"{job_id}/console.log")
        if content is None:
            return []
        return content.decode("utf-8").splitlines(keepends=True)

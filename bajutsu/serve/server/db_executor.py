"""A database-backed `RunExecutor` for the post-completion worker model (BE-0106).

Replaces `QueueExecutor` (RQ/Redis): instead of enqueuing onto a Redis queue, it inserts a
``queued`` row into the ``jobs`` table. A ``bajutsu worker`` leases it over HTTP. The repository
is **injected**, so no SQLAlchemy is imported at the top — safe to import and unit-test without the
``db`` extra."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bajutsu.serve.server.worker_job import job_spec

if TYPE_CHECKING:
    from bajutsu.serve.jobs import Job, ServeState
    from bajutsu.serve.server.db import Repository


class DbQueueExecutor:
    """Dispatches a job by inserting a ``queued`` row (the `RunExecutor` seam, BE-0106)."""

    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    def dispatch(self, state: ServeState, job: Job) -> None:
        self._repo.enqueue_job(job.id, org_id=job.org or "", spec=job_spec(job))

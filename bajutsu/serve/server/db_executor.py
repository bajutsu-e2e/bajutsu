"""A database-backed `RunExecutor` for the post-completion worker model (BE-0106).

Replaces `QueueExecutor` (RQ/Redis): instead of enqueuing onto a Redis queue, it inserts a
``queued`` row into the ``jobs`` table. A ``bajutsu worker`` leases it over HTTP. The repository
is **injected**, so no SQLAlchemy is imported at the top — safe to import and unit-test without the
``db`` extra."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bajutsu.serve.server.db import cancelled_job_result
from bajutsu.serve.server.worker_job import job_spec

if TYPE_CHECKING:
    from bajutsu.serve.server.db import Repository
    from bajutsu.serve.state import Job, ServeState


class DbQueueExecutor:
    """Dispatches a job by inserting a ``queued`` row (the `RunExecutor` seam, BE-0106)."""

    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    def dispatch(self, state: ServeState, job: Job) -> None:
        self._repo.enqueue_job(
            job.id, org_id=job.org or "", spec=job_spec(job), capabilities=job.capabilities
        )
        # The job is registered before its row exists, so a cancel can land in between, find no
        # row, and stop only the in-memory `Job` (BE-0449). Carry that cancel onto the row now, or
        # the queue would still hand the job to a worker and run it in full.
        if job.cancelled:
            outcome = self._repo.cancel_job(job.id)
            if outcome == ("cancelled", True):
                state.logbus.close(job.id, json.dumps(cancelled_job_result(job.id)))

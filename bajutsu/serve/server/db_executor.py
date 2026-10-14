"""A database-backed `RunExecutor` for the post-completion worker model (BE-0106).

Replaces `QueueExecutor` (RQ/Redis): instead of enqueuing onto a Redis queue, it inserts a
``queued`` row into the ``jobs`` table. A ``bajutsu worker`` leases it over HTTP. The repository
is **injected**, so no SQLAlchemy is imported at the top — safe to import and unit-test without the
``db`` extra."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bajutsu.serve.capabilities import routed_capabilities
from bajutsu.serve.server.db import cancelled_job_result
from bajutsu.serve.server.worker_job import job_spec

if TYPE_CHECKING:
    from bajutsu.serve.server.db import Repository
    from bajutsu.serve.state import Job, ServeState


class DbQueueExecutor:
    """Dispatches a job by inserting a ``queued`` row (the `RunExecutor` seam, BE-0106).

    *machine_id* is the server machine's identity (`local_keys.machine_id`). A job whose spec names
    its files by their paths on this machine is stored requiring ``machine:<id>``, so only a worker
    here leases it; None stamps nothing, for a caller that routes no such job.
    """

    def __init__(self, repository: Repository, *, machine_id: str | None = None) -> None:
        self._repo = repository
        self._machine_id = machine_id

    def dispatch(self, state: ServeState, job: Job) -> None:
        spec = job_spec(job)
        # The spec carries a `cwd` exactly when the worker would refuse the job off this machine
        # (`worker_job._spec_cwd`), so routing and that refusal judge the same jobs.
        job.capabilities = routed_capabilities(
            job.capabilities, names_host_paths=spec["cwd"] is not None, machine_id=self._machine_id
        )
        self._repo.enqueue_job(
            job.id, org_id=job.org or "", spec=spec, capabilities=job.capabilities
        )
        # The job is registered before its row exists, so a cancel can land in between, find no
        # row, and stop only the in-memory `Job` (BE-0449). Carry that cancel onto the row now, or
        # the queue would still hand the job to a worker and run it in full.
        if job.cancelled:
            outcome = self._repo.cancel_job(job.id)
            if outcome == ("cancelled", True):
                state.logbus.close(job.id, json.dumps(cancelled_job_result(job.id)))

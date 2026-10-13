"""The in-process executor, one daemon thread per job, for the kinds not yet moved to workers."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bajutsu.serve.state import Job, ServeState


class LocalExecutor:
    """Runs each job in-process on a daemon thread: the kinds `RoutingExecutor` keeps (BE-0449)."""

    def dispatch(self, state: ServeState, job: Job) -> None:
        # Local import breaks the jobs↔executor cycle.
        from bajutsu.serve.jobs import run_job_in_process

        # The thread reads the org's secrets into the child env, on the in-process path alone, so a
        # store that keeps them out of `os.environ` (the database of a local start) still reaches
        # the job, and a queued job's stored spec never carries them (BE-0449).
        threading.Thread(target=run_job_in_process, args=(state, job), daemon=True).start()

"""The executor of a state with no queue: it runs nothing and fails each job it is handed."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bajutsu.serve.state import Job, ServeState

# The line a job ends with when nothing could run it, so a person reading its console sees why.
UNQUEUED_LINE = (
    "error: this server has no job queue, so nothing can run the job; the server never runs a job "
    "itself (BE-0449) — start it with `bajutsu serve` and run a `bajutsu worker` beside it"
)


class UnqueuedExecutor:
    """Fails every job at dispatch, since the server never runs a job in its own process (BE-0449).

    `ServeState`'s default, for a state built without a queue: a worker's own state, which never
    dispatches, and a test that wires no executor. `bajutsu serve` always wires `DbQueueExecutor`.
    The job still ends through the usual tail, so its stream closes and its slot is released rather
    than stranded.
    """

    def dispatch(self, state: ServeState, job: Job) -> None:
        """End *job* at once with `UNQUEUED_LINE` and a failing exit code."""
        # Local import breaks the jobs↔executor cycle.
        from bajutsu.serve.jobs import fail_unrun_job

        fail_unrun_job(state, job, UNQUEUED_LINE)

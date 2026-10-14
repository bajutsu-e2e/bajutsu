"""The transition executor that sends each job kind to the queue or the in-process thread."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .local_executor import LocalExecutor

if TYPE_CHECKING:
    from bajutsu.serve.state import Job, JobKind, ServeState

    from .run_executor import RunExecutor


class RoutingExecutor:
    """Dispatches a job to *queue* when its kind is in *queued_kinds*, else to *local* (BE-0449).

    The seam the kinds move through, one at a time, from the server to the workers: widening
    *queued_kinds* moves a kind, and once it holds every kind the local executor can go. A job whose
    kind is unknown goes to *queue*: running an unrecognized command in the server's own process is
    the outcome this item removes, so an unknown kind fails closed rather than open.
    """

    def __init__(
        self, queue: RunExecutor, local: RunExecutor, queued_kinds: Iterable[JobKind]
    ) -> None:
        self._queue = queue
        self._local = local
        self._queued: frozenset[JobKind] = frozenset(queued_kinds)

    @property
    def queued_kinds(self) -> frozenset[JobKind]:
        """The kinds this executor hands to the queue."""
        return self._queued

    def routes_locally(self, kind: JobKind | None) -> bool:
        """Whether a job of *kind* runs on the local executor rather than the queue."""
        return kind is not None and kind not in self._queued

    def dispatch(self, state: ServeState, job: Job) -> None:
        """Hand *job* to the executor its kind routes to."""
        target = self._local if self.routes_locally(job.kind) else self._queue
        target.dispatch(state, job)


def runs_in_process(executor: RunExecutor, kind: JobKind) -> bool:
    """Whether *executor* runs a job of *kind* in the server's own process.

    A path that needs the lease worker (a per-job artifact override, BE-0431) asks this rather than
    testing the executor's type, so it keeps working under a `RoutingExecutor`.
    """
    if isinstance(executor, RoutingExecutor):
        return executor.routes_locally(kind)
    return isinstance(executor, LocalExecutor)

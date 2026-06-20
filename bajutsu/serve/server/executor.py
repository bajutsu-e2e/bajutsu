"""A queue-based `RunExecutor` for the hosted backend (BE-0015 server phase).

Where `LocalExecutor` runs each job on an in-process thread, `QueueExecutor` serializes the job and
**enqueues** it for a remote `bajutsu worker` to consume. It's the one server piece on the job
dispatch path; everything `run_job` does is unchanged.

The queue is **injected**, so the only thing that touches RQ/Redis is whoever constructs the real
queue (the worker / the server wiring) — this module imports neither, so it's safe to import (and
unit-test with a fake queue) without the ``worker`` extra installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from bajutsu.serve.server.worker_job import execute_job_spec, job_spec

if TYPE_CHECKING:
    from bajutsu.serve.jobs import Job, ServeState


class Queue(Protocol):
    """The slice of an RQ ``Queue`` that `QueueExecutor` needs (so a fake can stand in)."""

    def enqueue(self, func: object, *args: object, **kwargs: object) -> object:
        """Enqueue *func* with *args* for a worker to run; the return value is ignored."""


class QueueExecutor:
    """Dispatches a job by enqueuing its spec for a remote worker (the `RunExecutor` seam)."""

    def __init__(self, queue: Queue) -> None:
        self._queue = queue

    def dispatch(self, state: ServeState, job: Job) -> None:
        # The worker reconstructs the job from this spec and runs `run_job`; `state` stays on the
        # control plane (its popen/simctl/cwd are worker-side concerns).
        self._queue.enqueue(execute_job_spec, job_spec(job))

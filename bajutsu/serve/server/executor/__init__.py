"""An RQ/Redis-backed `RunExecutor` for the hosted backend (BE-0015 server phase, legacy).

`QueueExecutor` serializes the job and **enqueues** it for a remote `bajutsu worker` to consume;
everything `run_job` does is unchanged.
Superseded by `db_executor.DbQueueExecutor` (BE-0106), which dispatches without Redis and is what
the server backend wires today.

The queue is **injected**, so the only thing that touches RQ/Redis is whoever constructs the real
queue — this module imports neither, so it's safe to import (and unit-test with a fake queue)
without the ``worker`` extra installed.
"""

from .queue import Queue
from .queue_executor import QueueExecutor

__all__ = ["Queue", "QueueExecutor"]

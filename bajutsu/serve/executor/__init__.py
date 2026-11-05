"""The RunExecutor seam: how a created job gets executed (BE-0015, BE-0449).

`dispatch` hands a created job to whatever executes it. The server never executes a job itself
(BE-0449): `bajutsu serve` enqueues every job for a ``bajutsu worker`` (`DbQueueExecutor`,
BE-0106), and the worker runs it through `run_job`. A state with no queue fails each job instead
(`UnqueuedExecutor`), so no path spawns a job child in the server's process.
"""

from .run_executor import RunExecutor
from .unqueued_executor import UNQUEUED_LINE, UnqueuedExecutor

__all__ = ["UNQUEUED_LINE", "RunExecutor", "UnqueuedExecutor"]

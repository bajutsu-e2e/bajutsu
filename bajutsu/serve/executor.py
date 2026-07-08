"""The RunExecutor seam: how a created job gets executed (BE-0015 local/server parity).

`dispatch` is the single point where local and server hosting diverge. Locally each job runs
in-process on a daemon thread (`LocalExecutor`, today's behavior); a future server backend would
enqueue the job for a remote ``bajutsu worker`` instead. The execution body itself — boot, build,
run, stream — lives in `run_job` and is identical on both sides (locally the worker is just a
thread), so it stays put.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bajutsu.serve.state import Job, ServeState


class RunExecutor(Protocol):
    """Arranges for an already-created `Job` to be executed asynchronously."""

    def dispatch(self, state: ServeState, job: Job) -> None:
        """Arrange for *job* to be executed asynchronously."""


class LocalExecutor:
    """Runs each job in-process on a daemon thread — the default for `bajutsu serve`."""

    def dispatch(self, state: ServeState, job: Job) -> None:
        from bajutsu.serve.jobs import run_job  # local import breaks the jobs↔executor cycle

        threading.Thread(target=run_job, args=(state, job), daemon=True).start()

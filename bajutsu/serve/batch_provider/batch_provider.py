"""The seam a batch cloud runs one scenario through: submit, wait, and return the verdict."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from bajutsu.common.cloud.devicefarm import Verdict

from .batch_checkpoint import BatchCheckpoint
from .batch_request import BatchRequest


class BatchProvider(Protocol):
    """A batch cloud serve can run one scenario on: submit it, wait, and return Bajutsu's verdict.

    The verdict is read from the downloaded ``manifest.json`` tree (left under `dest`), so it is
    Bajutsu's own pass/fail, never the cloud's run classification — the provider stays off the
    `run`/CI verdict path.
    """

    def submit(
        self,
        request: BatchRequest,
        *,
        work_dir: Path,
        dest: Path,
        checkpoint: BatchCheckpoint | None = None,
        job_id: str = "",
    ) -> Verdict:
        """Package `work_dir`, run `request.scenario` on the cloud, download artifacts under `dest`.

        With a `checkpoint`, persist the scheduled run so a re-leased worker resumes polling it after a
        restart rather than resubmitting (BE-0336 Unit 5); None keeps the best-effort, non-durable path.
        """
        raise NotImplementedError

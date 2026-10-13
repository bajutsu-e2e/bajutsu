"""How many live log lines one job may publish, counted per server process (BE-0449)."""

from __future__ import annotations

import threading
from collections import OrderedDict

# A long run's console stays readable well inside this; past it, a looping job would only grow the
# bus (and a shared Redis) until its stream expires. The full log is always in console.log.
DEFAULT_LIVE_LINES_PER_JOB = 50_000
# The same bound in bytes, since 50,000 lines of up to 8 KiB each would still be 400 MiB per job.
DEFAULT_LIVE_BYTES_PER_JOB = 16 * 1024 * 1024
# Jobs tracked at once. A job that ends without its result reaching this process is never forgotten
# explicitly, so the oldest entry gives way instead of the table growing for the server's lifetime.
_MAX_TRACKED_JOBS = 4096


class LiveLineBudget:
    """Counts the live lines and bytes each job has published, and admits a batch up to both caps.

    The counts live in one process. On several replicas each one counts the lines posted to it, so
    the caps hold per replica, as a bound on that replica's memory rather than an exact total.
    """

    def __init__(
        self,
        per_job: int = DEFAULT_LIVE_LINES_PER_JOB,
        bytes_per_job: int = DEFAULT_LIVE_BYTES_PER_JOB,
    ) -> None:
        self._per_job = per_job
        self._bytes_per_job = bytes_per_job
        self._lock = threading.Lock()
        self._used: OrderedDict[str, tuple[int, int]] = OrderedDict()  # job -> (lines, bytes)
        self._truncated: set[str] = set()

    def admit(self, job_id: str, lines: list[str]) -> tuple[int, bool]:
        """How many of *lines*, from the front, *job_id* may publish, and whether this batch is the
        one that crossed a cap (so the caller publishes the truncation notice once)."""
        with self._lock:
            if job_id in self._truncated:
                # Closed for good once the notice went out: a smaller later line that would still
                # fit under the byte cap must not appear after the notice that ended the live log.
                return 0, False
            used_lines, used_bytes = self._used.get(job_id, (0, 0))
            allowed = 0
            for line in lines:
                size = len(line.encode("utf-8", "surrogatepass"))
                if used_lines >= self._per_job or used_bytes + size > self._bytes_per_job:
                    break
                used_lines += 1
                used_bytes += size
                allowed += 1
            self._used[job_id] = (used_lines, used_bytes)
            self._used.move_to_end(job_id)
            while len(self._used) > _MAX_TRACKED_JOBS:
                old, _ = self._used.popitem(last=False)
                self._truncated.discard(old)
            crossed = allowed < len(lines) and job_id not in self._truncated
            if crossed:
                self._truncated.add(job_id)
            return allowed, crossed

    def forget(self, job_id: str) -> None:
        """Drop *job_id*'s counts once its stream has closed."""
        with self._lock:
            self._used.pop(job_id, None)
            self._truncated.discard(job_id)

"""The log bus for the queue path: live lines from the worker, the uploaded log as the fallback.

A worker posts its log lines to ``/api/worker/log`` while the job runs (BE-0449), and the server
publishes them to a bus that accepts writes: `InMemoryLogBus` for one server process, or
`RedisLogBus` when ``BAJUTSU_REDIS_URL`` names a Redis that several replicas share. The worker's
uploaded ``console.log`` stays the durable copy, read the way `PostCompletionLogBus` reads it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Generator, Iterator
from typing import TYPE_CHECKING

from bajutsu.serve.logbus import InMemoryLogBus, LogBus
from bajutsu.serve.server.db import TERMINAL_JOB_STATUSES
from bajutsu.serve.server.logbus import RedisLike, RedisLogBus
from bajutsu.serve.server.post_completion_logbus import PostCompletionLogBus

if TYPE_CHECKING:
    from collections.abc import Callable

    from bajutsu.serve.artifacts import ArtifactStore
    from bajutsu.serve.server.db import Repository

REDIS_URL_ENV = "BAJUTSU_REDIS_URL"

# Finished jobs whose in-memory channel is kept for a viewer still reading it. A later viewer reads
# console.log instead, so the bound is about memory, not about who can see a log.
_KEEP_CLOSED = 256

_logger = logging.getLogger(__name__)


class QueueLogBus(PostCompletionLogBus):
    """A writable `LogBus` for queued jobs, closed by the worker's posted result (BE-0449).

    ``publish`` and ``close`` go to the *live* bus. The live lines serve a viewer who watches the
    job while it runs; the jobs table and the uploaded ``console.log`` cover the rest:

    - A viewer who subscribes after the job finished gets the uploaded ``console.log`` when it
      exists. The live backlog can have gaps (a dropped post, lines posted to another replica with
      no Redis) and can mix a reclaimed attempt's lines with the next attempt's, while
      ``console.log`` is the one complete copy.
    - A viewer who subscribes while the job runs gets the live lines. The stream ends when the live
      bus is closed, which the result route does. A job can also end with no close reaching this
      bus: a reclaim that fails it at the attempt cap posts no result, and on several replicas
      without Redis the result can land on another replica. So while the stream is idle it polls
      the jobs table, and once the job is terminal it drains what the live bus still holds and
      ends.
    - When not one live line reached that viewer by the end, the stream replays ``console.log``
      instead. When some live lines arrived, it does not append the log as well: the two copies
      would repeat each other, and a dropped post leaves a gap at no known place to merge on.
    - ``final`` prefers the payload the result route closed the live bus with, then the jobs table.
    - A live bus that raises (Redis unreachable) is logged once, and the stream and ``final`` fall
      back to the post-completion behavior this class extends. A stream that had already delivered
      live lines when the bus failed only waits for the job to finish and ends, for the same reason
      it never appends ``console.log`` to live lines.

    The in-memory channels are bounded: a job enters the eviction order at its first live lines and
    moves to the back when it closes, and past `_KEEP_CLOSED` jobs the oldest finished one is
    discarded — an unfinished one only when no finished job is left to drop. The
    live backlog then does not grow for the server's lifetime, even for jobs whose close never
    comes here; a Redis bus expires its own keys.
    """

    def __init__(
        self,
        repository: Repository,
        live: LogBus,
        artifacts_fn: Callable[[str], ArtifactStore | None] | None = None,
        *,
        poll_interval: float = 2.0,
        keep_closed: int = _KEEP_CLOSED,
    ) -> None:
        super().__init__(repository, artifacts_fn, poll_interval=poll_interval)
        self._live = live
        self._keep_closed = keep_closed
        self._closed: OrderedDict[str, bool] = OrderedDict()  # job -> finished
        self._closed_lock = threading.Lock()
        self._warned = False

    def publish(self, job_id: str, line: str) -> None:
        self._live.publish(job_id, line)

    def publish_many(self, job_id: str, lines: list[str]) -> None:
        self._live.publish_many(job_id, lines)
        # Tracked from its first lines, not only from its close: a job whose close never comes here
        # (its result went to another replica, its lease was failed at the attempt cap) is still
        # evicted in turn.
        self._track(job_id)

    def close(self, job_id: str, final: str | None = None) -> None:
        try:
            self._live.close(job_id, final)
        except (
            Exception
        ):  # the jobs table already holds the outcome; a live-bus fault can't undo it
            self._warn_live()
            if final is not None:
                super().close(job_id, final)
        self._retire(job_id)

    def final(self, job_id: str) -> str | None:
        try:
            live = self._live.final(job_id)
        except Exception:
            self._warn_live()
            live = None
        return live if live is not None else super().final(job_id)

    def stream(self, job_id: str, *, timeout: float | None = None) -> Iterator[str | None]:
        info = self._repo.get_job(job_id)
        if info is not None and info["status"] in TERMINAL_JOB_STATUSES:
            stored = self._console_log_of(info, job_id)
            if stored:
                yield from stored
                return
        delivered = [0]  # read after a mid-stream failure, which loses the generator's return value
        try:
            yield from self._live_lines(job_id, timeout, delivered)
        except Exception:
            self._warn_live()
            if delivered[0] == 0:
                yield from super().stream(job_id, timeout=timeout)
            else:
                yield from self._until_terminal(job_id, timeout)
            return
        self._retire(job_id)
        if delivered[0] == 0:
            info = self._repo.get_job(job_id)
            if info is not None and info["status"] in TERMINAL_JOB_STATUSES:
                yield from self._console_log_of(info, job_id)

    def _live_lines(
        self, job_id: str, timeout: float | None, delivered: list[int]
    ) -> Generator[str | None]:
        """The live bus's lines for *job_id*, ended by its close or the jobs table, counted into
        ``delivered[0]`` as each one is yielded."""
        idle = 0.0
        ending = False
        # The live bus is always read with the poll interval as its timeout, whatever the caller
        # asked for, so an idle stream wakes up to check the jobs table.
        live = self._live.stream(job_id, timeout=self._poll)
        try:
            for line in live:
                if line is not None:
                    delivered[0] += 1
                    idle = 0.0
                    yield line
                    continue
                if ending:
                    break  # terminal, and the live bus has gone quiet: every posted line is out
                if self._terminal(job_id):
                    # The worker posts its last lines before its result, so they are on the bus by
                    # now; one more pass reads any that arrived since the idle tick above.
                    ending = True
                    continue
                if timeout is not None:
                    idle += self._poll
                    if idle >= timeout:
                        idle = 0.0
                        yield None
        finally:
            if isinstance(live, Generator):
                live.close()

    def _until_terminal(self, job_id: str, timeout: float | None) -> Iterator[None]:
        """Heartbeats every *timeout* seconds until the jobs table shows *job_id* finished."""
        idle = 0.0
        while not self._terminal(job_id):
            time.sleep(self._poll)
            if timeout is not None:
                idle += self._poll
                if idle >= timeout:
                    idle = 0.0
                    yield None

    def _terminal(self, job_id: str) -> bool:
        info = self._repo.get_job(job_id)
        return info is not None and info["status"] in TERMINAL_JOB_STATUSES

    def _track(self, job_id: str) -> None:
        """Add *job_id* to the eviction order once, at its first lines, without reordering it."""
        if not isinstance(self._live, InMemoryLogBus):
            return
        with self._closed_lock:
            if job_id in self._closed:
                return
            self._closed[job_id] = False
            evicted = self._evict()
        for old in evicted:
            self._live.discard(old)

    def _retire(self, job_id: str) -> None:
        """Note that *job_id* finished, and discard the oldest channel past the bound."""
        if not isinstance(self._live, InMemoryLogBus):
            return  # Redis expires a finished job's keys itself
        with self._closed_lock:
            self._closed[job_id] = True
            self._closed.move_to_end(job_id)
            evicted = self._evict()
        for old in evicted:
            self._live.discard(old)

    def _evict(self) -> list[str]:
        """Victims past the bound, oldest finished job first, so a running job keeps its channel.

        Only when every entry left is unfinished does the oldest of them go: that is a job whose
        close never reaches this process (its result landed on another replica, or its worker
        vanished), and keeping it would let the backlog grow for the server's lifetime.
        """
        victims: list[str] = []
        while len(self._closed) > self._keep_closed:
            done = next((job for job, finished in self._closed.items() if finished), None)
            if done is None:
                done = next(iter(self._closed))
            del self._closed[done]
            victims.append(done)
        return victims

    def _warn_live(self) -> None:
        if not self._warned:
            self._warned = True
            _logger.warning(
                "the live log bus failed; serving logs from the jobs table and console.log",
                exc_info=True,
            )


def live_log_bus(
    redis_url: str | None, *, connect: Callable[[str], RedisLike] | None = None
) -> LogBus:
    """The writable bus a queue-backed server publishes worker log lines to (BE-0449).

    `InMemoryLogBus` unless *redis_url* is set, and then a `RedisLogBus` on that Redis, so a log
    line posted to one replica reaches a browser streaming from another. *connect* builds the
    client; by default it is redis-py's ``Redis.from_url``, imported only here.

    Raises:
        MissingServerExtra: *redis_url* is set but the ``redis`` package is not installed.
    """
    if not redis_url:
        return InMemoryLogBus()
    return RedisLogBus((connect or _redis_from_url)(redis_url))


def live_log_bus_from_env() -> LogBus:
    """`live_log_bus` for the URL in ``BAJUTSU_REDIS_URL``, if any."""
    return live_log_bus(os.environ.get(REDIS_URL_ENV) or None)


def _redis_from_url(url: str) -> RedisLike:
    try:
        import redis
    except ImportError as e:
        from bajutsu.serve import MissingServerExtra

        raise MissingServerExtra(
            f"{REDIS_URL_ENV} is set, but the redis package is not installed; "
            "install it with `pip install redis`"
        ) from e
    client: RedisLike = redis.Redis.from_url(url)
    return client

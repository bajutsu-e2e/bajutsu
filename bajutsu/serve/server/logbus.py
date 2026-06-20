"""A Redis-backed LogBus for the hosted backend (BE-0015 server phase).

`InMemoryLogBus` buffers each job's lines in one process. `RedisLogBus` keeps the same `LogBus`
contract — a late subscriber replays the whole log, the stream ends once the job is closed — but
stores the lines in Redis so the worker that runs the job and any control-plane replica serving
`/events` are different processes. It is the durable backlog the proposal's "live logs over Redis"
needs: a Redis **list** holds every line (so a late subscriber replays it) plus a **done** flag,
polled for the live tail.

The redis client is **injected** (the `RedisLike` slice below), so this module imports no redis —
it's safe to import and unit-test without the ``worker`` extra; the real client is wired in by the
worker / server selection.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Protocol

_LINES = "bajutsu:log:"  # Redis key prefix for a job's line list
_DONE = "bajutsu:logdone:"  # Redis key prefix for a job's "no more lines" flag
_DEFAULT_TTL = 86400  # seconds a finished job's log/done keys linger before Redis evicts them (24h)


class RedisLike(Protocol):
    """The slice of a redis-py client `RedisLogBus` uses (so a fake can stand in)."""

    def rpush(self, key: str, value: str) -> object:
        """Append *value* to the list at *key*."""

    def lrange(self, key: str, start: int, end: int) -> list[object]:
        """Return the list at *key* from index *start* to *end* (`-1` = last)."""

    def set(self, key: str, value: str) -> object:
        """Set *key* to *value*."""

    def get(self, key: str) -> object:
        """Return *key*'s value, or None if unset."""

    def expire(self, key: str, seconds: int) -> object:
        """Set *key* to expire in *seconds* (bounding a finished job's log lifetime)."""


class RedisLogBus:
    """LogBus backed by a Redis list (durable backlog) + a done flag, polled for the live tail.

    Cross-process and replica-safe: the worker `publish`es while it runs the job; any control-plane
    process can `stream` the same job id. `poll_interval` is how long `stream` waits before
    re-reading when no new line is available yet.
    """

    def __init__(
        self, redis: RedisLike, *, poll_interval: float = 0.1, ttl: int = _DEFAULT_TTL
    ) -> None:
        self._redis = redis
        self._poll = poll_interval
        self._ttl = ttl  # lifetime set on a job's keys at close() so finished logs self-clean

    @staticmethod
    def _text(value: object) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    def publish(self, job_id: str, line: str) -> None:
        self._redis.rpush(_LINES + job_id, line)

    def close(self, job_id: str, final: str | None = None) -> None:
        # The done key's presence ends the stream; its value carries the terminal status payload
        # (a JSON view) when given, or the bare "1" sentinel when not.
        self._redis.set(_DONE + job_id, final if final is not None else "1")
        # Bound both keys' lifetime from completion (not from the first line, so a long run's log
        # isn't evicted mid-stream): after `ttl` the backlog self-cleans rather than living forever.
        self._redis.expire(_LINES + job_id, self._ttl)
        self._redis.expire(_DONE + job_id, self._ttl)

    def final(self, job_id: str) -> str | None:
        value = self._redis.get(_DONE + job_id)
        if value is None:
            return None
        text = self._text(value)
        return None if text == "1" else text  # "1" = closed without a payload

    def stream(self, job_id: str, *, timeout: float | None = None) -> Iterator[str | None]:
        key = _LINES + job_id
        seen = 0
        idle = 0.0  # seconds since the last line, to pace heartbeats when *timeout* is set
        while True:
            batch = self._redis.lrange(key, seen, -1)
            seen += len(batch)
            if batch:
                idle = 0.0
                for line in batch:
                    yield self._text(line)
                continue
            # `close` is set only after every line is published. But a producer can rpush the final
            # lines and set the done flag between our (empty) lrange above and this check — so on
            # seeing done, re-drain once and only end when that tail is empty, never dropping it.
            if self._redis.get(_DONE + job_id) is not None:
                tail = self._redis.lrange(key, seen, -1)
                if not tail:
                    return
                seen += len(tail)
                for line in tail:
                    yield self._text(line)
                continue
            time.sleep(self._poll)
            if timeout is not None:
                idle += self._poll
                if idle >= timeout:
                    idle = 0.0
                    yield None  # idle for `timeout` with no new line → heartbeat

"""Tests for the Redis-backed LogBus (BE-0015 server phase).

`RedisLogBus` is the server implementation of the `LogBus` seam: it must preserve the same
contract as `InMemoryLogBus` (a late subscriber replays the whole log; the stream ends once the
job is closed; channels are isolated per job) but back it by Redis so any control-plane replica
can stream any worker's job log. The redis client is injected, so a small in-memory fake drives
the contract here — no real Redis on the gate.
"""

from __future__ import annotations

import threading

from bajutsu.serve.server.logbus import RedisLogBus


class FakeRedis:
    """The slice of a Redis client RedisLogBus uses, in memory. Returns bytes, like redis-py."""

    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}
        self._kv: dict[str, str] = {}

    def rpush(self, key: str, value: str) -> int:
        self._lists.setdefault(key, []).append(value)
        return len(self._lists[key])

    def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        items = self._lists.get(key, [])
        stop = len(items) if end == -1 else end + 1
        return [s.encode() for s in items[start:stop]]

    def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    def get(self, key: str) -> bytes | None:
        v = self._kv.get(key)
        return v.encode() if v is not None else None


def test_replays_backlog_then_ends_on_close() -> None:
    bus = RedisLogBus(FakeRedis())
    bus.publish("j1", "line A")
    bus.publish("j1", "line B")
    bus.close("j1")
    # A late subscriber still replays everything buffered, then the stream ends.
    assert list(bus.stream("j1")) == ["line A", "line B"]


def test_streams_live_lines() -> None:
    bus = RedisLogBus(FakeRedis(), poll_interval=0.01)
    got: list[str] = []

    def consume() -> None:
        got.extend(bus.stream("j2"))

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    bus.publish("j2", "first")
    bus.publish("j2", "second")
    bus.close("j2")
    t.join(timeout=2)
    assert not t.is_alive(), "stream did not end after close()"
    assert got == ["first", "second"]


def test_channels_are_isolated_by_job_id() -> None:
    bus = RedisLogBus(FakeRedis())
    bus.publish("a", "for a")
    bus.close("a")
    bus.publish("b", "for b")
    bus.close("b")
    assert list(bus.stream("a")) == ["for a"]
    assert list(bus.stream("b")) == ["for b"]


def test_close_records_final_status_and_stream_still_ends() -> None:
    # The terminal status rides the existing done key (its presence still ends the stream); `final`
    # returns the payload, or None when close carried none (the bare "1" sentinel).
    bus = RedisLogBus(FakeRedis())
    bus.publish("j", "line")
    bus.close("j", '{"status": "done", "ok": true}')
    assert list(bus.stream("j")) == ["line"]  # presence of the done key still ends the stream
    assert bus.final("j") == '{"status": "done", "ok": true}'
    plain = RedisLogBus(FakeRedis())
    plain.close("k")
    assert plain.final("k") is None  # closed without a payload


def test_stream_timeout_yields_heartbeat_then_line() -> None:
    import threading
    import time

    bus = RedisLogBus(FakeRedis(), poll_interval=0.01)
    got: list[str | None] = []

    def consume() -> None:
        got.extend(bus.stream("j", timeout=0.03))

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(0.12)  # idle long enough for a heartbeat
    bus.publish("j", "x")
    bus.close("j")
    t.join(timeout=2)
    assert not t.is_alive()
    assert None in got  # idle heartbeat
    assert "x" in got

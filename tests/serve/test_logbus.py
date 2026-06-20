"""Tests for the LogBus seam (BE-0015 live-log delivery, PR2).

`LogBus` is the one point where live-log delivery diverges between local and server hosting:
in-memory now (`InMemoryLogBus`), a Redis pub/sub stream later. A subscriber gets the backlog
already published plus any live lines, and the stream ends when the job is closed.
"""

from __future__ import annotations

import threading

from bajutsu import serve as srv


def test_inmemory_logbus_replays_backlog_then_ends_on_close() -> None:
    bus = srv.InMemoryLogBus()
    bus.publish("j1", "line A")
    bus.publish("j1", "line B")
    bus.close("j1")
    # A late subscriber still gets everything that was buffered, then the stream ends.
    assert list(bus.stream("j1")) == ["line A", "line B"]


def test_inmemory_logbus_streams_live_lines() -> None:
    bus = srv.InMemoryLogBus()
    got: list[str] = []
    err: list[Exception] = []

    def consume() -> None:
        try:
            got.extend(bus.stream("j2"))
        except Exception as exc:
            err.append(exc)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    bus.publish("j2", "first")
    bus.publish("j2", "second")
    bus.close("j2")
    t.join(timeout=2)
    assert not t.is_alive(), "stream did not end after close()"
    assert not err, f"consumer thread raised exception: {err[0]!r}"
    assert got == ["first", "second"]


def test_inmemory_logbus_channels_are_isolated() -> None:
    bus = srv.InMemoryLogBus()
    bus.publish("a", "for a")
    bus.close("a")
    bus.publish("b", "for b")
    bus.close("b")
    assert list(bus.stream("a")) == ["for a"]
    assert list(bus.stream("b")) == ["for b"]


def test_inmemory_logbus_records_final_status_on_close() -> None:
    # close() may carry the job's terminal status payload, which `final` returns (used for the
    # `done` event / poll); a close without one leaves final None.
    bus = srv.InMemoryLogBus()
    bus.publish("j", "line")
    bus.close("j", '{"status": "done", "ok": true}')
    assert bus.final("j") == '{"status": "done", "ok": true}'
    plain = srv.InMemoryLogBus()
    plain.close("k")
    assert plain.final("k") is None


def test_inmemory_stream_timeout_yields_heartbeat_then_line() -> None:
    # With a timeout, an idle stream yields None heartbeats (so a caller can keepalive / check for
    # disconnect); a real line still arrives, and the stream ends on close. Without a timeout the
    # stream never yields None (covered by the other tests).
    import threading
    import time

    bus = srv.InMemoryLogBus()
    got: list[str | None] = []

    def consume() -> None:
        got.extend(bus.stream("j", timeout=0.02))

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(0.1)  # stay idle so the timeout fires at least once
    bus.publish("j", "x")
    bus.close("j")
    t.join(timeout=2)
    assert not t.is_alive()
    assert None in got  # at least one idle heartbeat
    assert "x" in got  # the real line is still delivered

"""Tests for the idb backend: describe-all parsing, commands, coordinate tap."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.drivers.idb import (
    IdbDriver,
    parse_describe_all,
    tap_cmd,
)

FIXTURE = """
[
  {"AXUniqueId":"settings.open","AXLabel":"設定","type":"Button","enabled":true,
   "frame":{"x":0,"y":0,"width":100,"height":40}},
  {"AXUniqueId":"submit","AXLabel":"送信","type":"Button","enabled":false,
   "frame":{"x":0,"y":50,"width":100,"height":40}},
  {"AXLabel":"static","type":"StaticText","frame":{"x":0,"y":100,"width":100,"height":20}}
]
"""

NDJSON = (
    '{"AXUniqueId":"a","AXLabel":"A","type":"Button","frame":{"x":1,"y":2,"width":3,"height":4}}\n'
    '{"AXUniqueId":"b","AXLabel":"B","type":"Cell","frame":{"x":0,"y":0,"width":1,"height":1}}\n'
)


def test_parse_describe_all() -> None:
    els = parse_describe_all(FIXTURE)
    assert len(els) == 3
    assert els[0]["identifier"] == "settings.open"
    assert els[0]["label"] == "設定"
    assert els[0]["traits"] == ["button"]
    assert els[0]["frame"] == (0.0, 0.0, 100.0, 40.0)
    assert base.Trait.NOT_ENABLED in els[1]["traits"]  # enabled: false
    assert els[2]["identifier"] is None  # static text, no AXUniqueId


def test_parse_ndjson_fallback() -> None:
    els = parse_describe_all(NDJSON)
    assert [e["identifier"] for e in els] == ["a", "b"]


def test_tap_resolves_frame_center() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            return FIXTURE
        calls.append(args)
        return ""

    driver = IdbDriver("U", run=run)
    driver.tap({"id": "settings.open"})
    # center of (0,0,100,40) -> (50, 20)
    assert calls == [tap_cmd("U", 50, 20)]
    # A short dwell is always included so the tap actuates a UISwitch (see idb.py).
    assert calls[0] == ["idb", "ui", "tap", "--udid", "U", "50", "20", "--duration", "0.1"]


def test_capabilities_has_no_semantic_tap() -> None:
    assert base.Capability.SEMANTIC_TAP not in IdbDriver("U", run=lambda a: "[]").capabilities()


# A near-empty tree as idb returns mid-transition: one element, no identifier.
EMPTY = '[{"AXLabel":"","frame":{"x":0,"y":0,"width":0,"height":0}}]'


def _scripted(responses: list[str]) -> tuple[object, list[int]]:
    """A run() that returns describe-all responses in order (one per describe-all call),
    holding the last once exhausted. Returns (run, calls) where calls[0] counts
    describe-all invocations."""
    seq = list(responses)
    calls = [0]

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            calls[0] += 1
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""

    return run, calls


def test_query_retries_through_transient_empty() -> None:
    # Full tree first (establishes a richer baseline), then a transient empty, then full.
    run, calls = _scripted([FIXTURE, EMPTY, FIXTURE])
    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0  # no real sleeping in the test

    assert len(driver.query()) == 3  # baseline: _max_seen becomes 3
    els = driver.query()  # hits EMPTY then recovers to the full tree
    assert len(els) == 3
    assert els[0]["identifier"] == "settings.open"
    assert calls[0] == 3  # 1 baseline + (1 empty + 1 recovered)


def test_query_does_not_retry_genuinely_sparse_screen() -> None:
    # No richer tree has ever been seen, so a small tree is taken at face value.
    run, calls = _scripted([EMPTY])
    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0

    assert len(driver.query()) == 1
    assert calls[0] == 1  # returned immediately, no retry


def test_query_returns_after_bounded_retries_when_empty_persists() -> None:
    # After a rich tree, a persistent empty tree is retried a bounded number of
    # times and then returned — query() must not hang masking a real empty screen.
    run, calls = _scripted([FIXTURE, EMPTY])
    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0

    assert len(driver.query()) == 3  # baseline
    calls[0] = 0
    assert len(driver.query()) == 1  # gives up and returns the empty tree
    assert calls[0] == 1 + IdbDriver._EMPTY_RETRIES  # initial + bounded retries


def test_empty_backoff_grows_exponentially_then_caps() -> None:
    # The transient-empty retry backs off exponentially (recovering fast when the empty
    # clears quickly, spacing out later) and caps so the total stays within the prior bound.
    driver = IdbDriver("U", run=lambda a: "[]")
    seq = [driver._empty_backoff(i) for i in range(IdbDriver._EMPTY_RETRIES)]
    assert seq == [0.05, 0.1, 0.2, 0.2, 0.2]  # base 0.05, doubling, capped at 0.2
    assert sum(seq) <= 1.0  # no further than the previous fixed-0.2 * 5 bound


def test_wait_for_returns_true_when_already_present() -> None:
    driver = IdbDriver("U", run=lambda a: FIXTURE)
    assert driver.wait_for({"id": "settings.open"}, timeout=1, poll=0) is True


def test_wait_for_polls_until_the_element_appears() -> None:
    # Absent on the first reads, then it shows up: wait_for must keep polling, not check once.
    run, calls = _scripted([EMPTY, EMPTY, FIXTURE])
    driver = IdbDriver("U", run=run)
    assert driver.wait_for({"id": "settings.open"}, timeout=5, poll=0) is True
    assert calls[0] >= 3  # polled past the empty trees until the element appeared


def test_wait_for_times_out_when_absent() -> None:
    driver = IdbDriver("U", run=lambda a: "[]")
    assert driver.wait_for({"id": "nope"}, timeout=0, poll=0) is False

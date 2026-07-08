"""Tests for the XCUITest backend's Python channel client (BE-0019 Slice 2).

The driver actuates over a loopback HTTP channel to a resident XCTest runner. The runner itself is a
later, on-device slice; here the request/response logic is exercised against an injected fake
transport (mirroring how the idb driver injects a fake `run`), so nothing on the gate needs a
Simulator. Resolution stays Python-side, so the key property is that the driver acts on **exactly**
the element it resolved, addressed by that element's per-snapshot handle.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.xcuitest import (
    _MAX_ATTEMPTS,
    _SOCKET_TIMEOUT_SECONDS,
    TransportFn,
    XcuitestChannelError,
    XcuitestDriver,
    _decode,
    _is_retry_eligible,
    _Reply,
    _TransportFailure,
    _with_retry,
)


def _el_wire(
    handle: str,
    identifier: str | None = None,
    label: str | None = None,
    value: str | None = None,
    traits: list[str] | None = None,
    frame: tuple[float, float, float, float] = (0.0, 0.0, 10.0, 10.0),
) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "label": label,
        "value": value,
        "traits": traits or [],
        "frame": list(frame),
        "handle": handle,
    }


def _elements(*els: dict[str, Any]) -> _Reply:
    return _Reply(status="ok", elements=list(els))


def _driver(transport: TransportFn) -> XcuitestDriver:
    return XcuitestDriver(transport=transport)


def test_driver_satisfies_the_protocol() -> None:
    assert isinstance(_driver(lambda m, p, b: _Reply(status="ok")), base.Driver)


def test_query_parses_elements_and_does_not_leak_the_handle() -> None:
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        assert (method, path) == ("GET", "/elements")
        return _elements(
            _el_wire("h-title", "home.title", "Home"),
            _el_wire("h-ok", "ok", "OK", traits=["button"]),
        )

    els = _driver(transport).query()
    assert els == [
        {
            "identifier": "home.title",
            "label": "Home",
            "value": None,
            "traits": [],
            "frame": (0.0, 0.0, 10.0, 10.0),
        },
        {
            "identifier": "ok",
            "label": "OK",
            "value": None,
            "traits": ["button"],
            "frame": (0.0, 0.0, 10.0, 10.0),
        },
    ]
    assert all("handle" not in el for el in els)  # the handle is not a selector/Element field


def test_tap_resolves_unique_then_sends_that_elements_snapshot_handle() -> None:
    sent: list[tuple[str, str, dict[str, Any] | None]] = []

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                _el_wire("h-title", "home.title", "Home"),
                _el_wire("h-ok", "ok", "OK", traits=["button"]),
            )
        sent.append((method, path, body))
        return _Reply(status="ok")

    _driver(transport).tap({"id": "ok"})
    assert sent == [
        ("POST", "/tap", {"handle": "h-ok"})
    ]  # the resolved element's handle, not coords


def test_pinch_and_rotate_emit_gesture_requests_with_the_handle() -> None:
    sent: list[tuple[str, dict[str, Any] | None]] = []

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-img", "photo", "Photo"))
        sent.append((path, body))
        return _Reply(status="ok")

    d = _driver(transport)
    d.pinch({"id": "photo"}, 2.0)
    d.rotate({"id": "photo"}, 1.57)
    assert sent == [
        ("/gesture", {"handle": "h-img", "kind": "pinch", "scale": 2.0}),
        ("/gesture", {"handle": "h-img", "kind": "rotate", "radians": 1.57}),
    ]


def test_capabilities_add_semantic_tap_condition_wait_multi_touch_but_not_network() -> None:
    caps = _driver(lambda m, p, b: _Reply(status="ok")).capabilities()
    assert base.Capability.SEMANTIC_TAP in caps
    assert base.Capability.CONDITION_WAIT in caps
    assert base.Capability.MULTI_TOUCH in caps  # the gestures idb cannot do
    # Network evidence rides on the app-side collector, not the actuator (proposal: BE-0020 boundary).
    assert base.Capability.NETWORK not in caps


def test_stale_handle_raises_the_vanished_element_error() -> None:
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        return _Reply(status="stale")  # the screen changed under the resolved handle

    with pytest.raises(base.ElementNotFound):
        _driver(transport).tap({"id": "ok"})


def test_ambiguous_selector_fails_before_any_actuation_request() -> None:
    calls: list[str] = []

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        calls.append(path)
        if path == "/elements":
            return _elements(
                _el_wire("h1", "dup", "A", traits=["button"]),
                _el_wire("h2", "dup", "B", traits=["button"]),
            )
        return _Reply(status="ok")

    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).tap({"id": "dup"})
    assert calls == ["/elements"]  # selection is Python-side; no /tap was ever sent


def test_missing_selector_fails_before_any_actuation_request() -> None:
    calls: list[str] = []

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        calls.append(path)
        return _elements(_el_wire("h-ok", "ok", "OK"))

    with pytest.raises(base.ElementNotFound):
        _driver(transport).tap({"id": "absent"})
    assert calls == ["/elements"]


def test_screenshot_writes_the_returned_png_bytes(tmp_path: Any) -> None:
    png = b"\x89PNG\r\n\x1a\nfake-bytes"
    out = tmp_path / "shot.png"
    _driver(lambda m, p, b: _Reply(status="ok", png=png)).screenshot(str(out))
    assert out.read_bytes() == png


def test_wait_until_polls_xcuitest_until_a_match_appears() -> None:
    # BE-0118: wait_for is single-shot; the shared wait_until owns the poll. It must keep
    # polling xcuitest past the empty snapshot until the element renders.
    snapshots = [[], [_el_wire("h-ok", "ok", "OK")]]  # first empty, then the element renders

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=snapshots.pop(0) if snapshots else [])

    assert base.wait_until(_driver(transport), {"id": "ok"}, timeout=1.0, poll=0) is True


def test_wait_for_is_single_shot() -> None:
    present = _driver(lambda m, p, b: _Reply(status="ok", elements=[_el_wire("h-ok", "ok", "OK")]))
    assert present.wait_for({"id": "ok"}) is True
    absent = _driver(lambda m, p, b: _Reply(status="ok", elements=[]))
    assert absent.wait_for({"id": "never"}) is False


def test_await_ready_returns_once_the_runner_health_is_ready() -> None:
    _driver(lambda m, p, b: _Reply(status="ready")).await_ready(timeout=1.0, poll=0.001)


def test_await_ready_times_out_loudly_when_the_runner_never_comes_up() -> None:
    with pytest.raises(XcuitestChannelError, match="did not come up"):
        _driver(lambda m, p, b: _Reply(status="starting")).await_ready(timeout=0.02, poll=0.001)


def test_a_runner_crash_mid_action_fails_loudly_not_as_not_found() -> None:
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        raise XcuitestChannelError("connection refused")  # runner exited mid-run

    with pytest.raises(XcuitestChannelError):
        _driver(transport).tap({"id": "ok"})


# --- the wire decode (pure; the only socket I/O, _http_transport, is the thin untested edge) --- #


def test_decode_elements_response_keeps_status_and_handles() -> None:
    body = json.dumps(
        {"status": "ok", "elements": [{"identifier": "ok", "handle": "h-ok"}]}
    ).encode()
    reply = _decode("/elements", 200, body)
    assert reply.status == "ok"
    assert reply.elements is not None and reply.elements[0]["handle"] == "h-ok"


def test_decode_screenshot_returns_raw_png_bytes() -> None:
    reply = _decode("/screenshot", 200, b"\x89PNGraw")
    assert reply.png == b"\x89PNGraw"


def test_decode_non_200_carries_the_servers_status() -> None:
    reply = _decode("/tap", 404, json.dumps({"status": "not-found"}).encode())
    assert reply.status == "not-found"


def test_an_element_without_a_handle_is_a_loud_channel_error() -> None:
    # A malformed /elements item (no handle) must fail loudly, not be coerced to "" and sent back.
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=[{"identifier": "ok", "frame": [0, 0, 1, 1]}])

    with pytest.raises(XcuitestChannelError, match="without a handle"):
        _driver(transport).query()


def test_a_runner_error_status_is_an_infra_failure_not_element_not_found() -> None:
    # A non-outcome status (e.g. an "error" decoded from a 500 / malformed response) is a runner
    # failure — it must surface as XcuitestChannelError, never be masked as element-not-found.
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        return _Reply(status="error")

    with pytest.raises(XcuitestChannelError):
        _driver(transport).tap({"id": "ok"})


def test_a_runner_not_found_status_is_a_test_outcome() -> None:
    # `not-found` from the runner is a test outcome (the element could not be actuated), so it maps
    # to the shared ElementNotFound, distinct from an infrastructure error.
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        return _Reply(status="not-found")

    with pytest.raises(base.ElementNotFound):
        _driver(transport).tap({"id": "ok"})


def test_screenshot_fails_loudly_on_a_runner_error(tmp_path: Any) -> None:
    out = tmp_path / "never.png"
    with pytest.raises(XcuitestChannelError):
        _driver(lambda m, p, b: _Reply(status="error", png=None)).screenshot(str(out))
    assert not out.exists()  # no bogus artifact written


def test_socket_timeout_is_bounded_after_the_single_snapshot_query() -> None:
    # BE-0105 replaced the ~10s+ per-attribute /elements walk with one app.snapshot(), so the
    # generous 60s stopgap is no longer needed: the timeout must stay bounded to a reasonable window
    # (it still covers a cold first snapshot) so a wedged runner fails loudly rather than hanging.
    assert 0 < _SOCKET_TIMEOUT_SECONDS <= 30


# --- transient-transport retry policy (BE-0207) --- #
#
# The retry lives behind the `TransportFn` seam: `_with_retry` wraps a single-attempt transport and
# re-issues only *transport* failures (`_TransportFailure`), never a decoded outcome. It is exercised
# here with a fake inner transport (no Simulator), passing a no-op `sleep` so backoff adds no wall time.


def _counting(replies: list) -> tuple[TransportFn, list[int]]:
    """A fake inner transport that yields *replies* in order; each item is either a `_Reply` to return
    or a `_TransportFailure` to raise. `calls[0]` counts how many times it was invoked."""
    calls = [0]

    def inner(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        calls[0] += 1
        item = replies.pop(0)
        if isinstance(item, _TransportFailure):
            raise item
        return item

    return inner, calls


def test_is_retry_eligible_splits_on_delivery_and_idempotency() -> None:
    # Not delivered → the runner never acted, safe to re-issue any method.
    assert _is_retry_eligible("POST", delivered=False) is True
    assert _is_retry_eligible("GET", delivered=False) is True
    # Delivered → only idempotent reads may be re-issued; a write could double-apply.
    assert _is_retry_eligible("GET", delivered=True) is True
    assert _is_retry_eligible("POST", delivered=True) is False


def test_transient_read_failure_retries_then_succeeds() -> None:
    inner, calls = _counting([_TransportFailure("timed out", delivered=True), _Reply(status="ok")])
    reply = _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert reply.status == "ok"
    assert calls[0] == 2  # first attempt failed, second succeeded


def test_write_that_times_out_after_delivery_is_not_re_sent() -> None:
    # A POST whose response timed out *after* the request was delivered must not be re-issued — that
    # could double-apply the gesture. It fails loudly on the first attempt instead.
    inner, calls = _counting([_TransportFailure("timed out", delivered=True), _Reply(status="ok")])
    with pytest.raises(XcuitestChannelError, match="POST /gesture"):
        _with_retry(inner, sleep=lambda _s: None)("POST", "/gesture", {"kind": "pinch"})
    assert calls[0] == 1  # no retry: the second (success) reply was never reached


def test_write_that_never_reached_the_runner_is_retried() -> None:
    # A connect/send failure means the runner never acted, so even a POST is safe to re-issue.
    inner, calls = _counting([_TransportFailure("refused", delivered=False), _Reply(status="ok")])
    reply = _with_retry(inner, sleep=lambda _s: None)("POST", "/gesture", {"kind": "pinch"})
    assert reply.status == "ok"
    assert calls[0] == 2


def test_persistent_failure_exhausts_attempts_and_fails_loudly() -> None:
    inner, calls = _counting([_TransportFailure("refused", delivered=False)] * (_MAX_ATTEMPTS + 2))
    with pytest.raises(XcuitestChannelError, match="GET /elements failed: refused"):
        _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert calls[0] == _MAX_ATTEMPTS  # exactly the bounded number of attempts, no more


def test_a_decoded_outcome_reply_is_never_retried() -> None:
    # `stale` / `not-found` are decoded outcomes (a `_Reply`, not a `_TransportFailure`), so the
    # retry seam returns them untouched — retrying an outcome is exactly the absorption BE-0049 rejects.
    inner, calls = _counting([_Reply(status="not-found")])
    reply = _with_retry(inner, sleep=lambda _s: None)("POST", "/tap", {"handle": "h"})
    assert reply.status == "not-found"
    assert calls[0] == 1


def test_each_retry_emits_a_diagnostic(caplog: Any) -> None:
    inner, _calls = _counting([_TransportFailure("timed out", delivered=True), _Reply(status="ok")])
    with caplog.at_level("WARNING"):
        _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert any("GET /elements" in r.message and "1/" in r.message for r in caplog.records)


def test_retry_knobs_are_bounded() -> None:
    # A small fixed attempt count, per BE-0207: enough to ride out a brief stall, not so many that a
    # wedged runner is retried for an unbounded stretch.
    assert 1 < _MAX_ATTEMPTS <= 5

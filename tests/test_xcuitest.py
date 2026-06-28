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
    TransportFn,
    XcuitestChannelError,
    XcuitestDriver,
    _decode,
    _Reply,
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


def test_wait_for_polls_until_a_match_appears() -> None:
    snapshots = [[], [_el_wire("h-ok", "ok", "OK")]]  # first empty, then the element renders

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=snapshots.pop(0) if snapshots else [])

    assert _driver(transport).wait_for({"id": "ok"}, timeout=1.0, poll=0.001) is True


def test_wait_for_returns_false_on_timeout() -> None:
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=[])

    assert _driver(transport).wait_for({"id": "never"}, timeout=0.02, poll=0.001) is False


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

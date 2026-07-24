"""Tests for the XCUITest backend's Python channel client (BE-0019 Slice 2).

The driver actuates over a loopback HTTP channel to a resident XCTest runner. The runner itself is a
later, on-device slice; here the request/response logic is exercised against an injected fake
transport (mirroring how the adb driver injects a fake `run`), so nothing on the gate needs a
Simulator. Resolution stays Python-side, so the key property is that the driver acts on **exactly**
the element it resolved, addressed by that element's per-snapshot handle.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.xcuitest import (
    _ACTUATION_TIMEOUT_SECONDS,
    _MAX_ATTEMPTS,
    _RECOVERY_TIMEOUT_SECONDS,
    _SOCKET_TIMEOUT_SECONDS,
    _STALE_MAX_ATTEMPTS,
    TransportFn,
    XcuitestChannelError,
    XcuitestDriver,
    XcuitestRunnerCrashError,
    _await_health,
    _decode,
    _is_retry_eligible,
    _raw_http_transport,
    _Reply,
    _timeout_for,
    _TransportFailure,
    _with_crash_recovery,
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
    # No-op sleep so the BE-0289 stale re-resolution backoff adds no wall time on the gate.
    return XcuitestDriver(transport=transport, sleep=lambda _s: None)


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


def test_back_taps_the_os_back_button() -> None:
    # iOS has no hardware back: `back` resolves and taps the OS navigation back button
    # (identifier "BackButton") — BE-0210.
    sent: list[tuple[str, str, dict[str, Any] | None]] = []

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-back", "BackButton", "Back", traits=["button"]))
        sent.append((method, path, body))
        return _Reply(status="ok")

    _driver(transport).back()
    assert sent == [("POST", "/tap", {"handle": "h-back"})]


def test_scroll_delegates_to_a_real_swipe_drag() -> None:
    # A directional scroll on iOS is a real XCUITest drag, so scroll delegates to swipe (BE-0227) —
    # the same POST /swipe an XCUITest drag issues, since a drag already scrolls scroll views.
    sent: list[tuple[str, str, dict[str, Any] | None]] = []

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        sent.append((method, path, body))
        return _Reply(status="ok")

    _driver(transport).scroll((10.0, 20.0), (30.0, 40.0))
    assert sent == [("POST", "/swipe", {"from": [10.0, 20.0], "to": [30.0, 40.0]})]


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


def test_text_editing_requests_carry_the_action_payload() -> None:
    # delete/select/copy each POST to their own endpoint; the runner types the native key or key
    # chord on the focused field (BE-0265). Focus is a prior tap the orchestrator issues.
    sent: list[tuple[str, str, dict[str, Any] | None]] = []

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        sent.append((method, path, body))
        return _Reply(status="ok")

    d = _driver(transport)
    d.delete_text(3)
    d.select_all()
    d.copy_selection()
    assert sent == [
        ("POST", "/deleteText", {"count": 3}),
        ("POST", "/selectAll", {}),
        ("POST", "/copy", {}),
    ]


def test_text_editing_raises_on_a_non_ok_reply() -> None:
    # A failed actuation is loud on every text-editing endpoint, never a silent no-op (determinism
    # first) — the three share one guard shape, so pin all three.
    d = _driver(lambda m, p, b: _Reply(status="error"))
    with pytest.raises(XcuitestChannelError):
        d.delete_text(1)
    with pytest.raises(XcuitestChannelError):
        d.select_all()
    with pytest.raises(XcuitestChannelError):
        d.copy_selection()


def test_select_option_unsupported() -> None:
    # <select> is a web control with no iOS-native counterpart, so the backend refuses (BE-0191).
    d = _driver(lambda m, p, b: _Reply(status="ok"))
    with pytest.raises(base.UnsupportedAction):
        d.select_option({"id": "nav.theme-picker"}, "midnight")


def test_capabilities_add_semantic_tap_condition_wait_multi_touch_but_not_network() -> None:
    caps = _driver(lambda m, p, b: _Reply(status="ok")).capabilities()
    assert base.Capability.SEMANTIC_TAP in caps
    assert base.Capability.CONDITION_WAIT in caps
    assert base.Capability.MULTI_TOUCH in caps  # two-finger gestures
    assert base.Capability.TEXT_SELECTION in caps  # select/copy actuate (BE-0280)
    # Network evidence rides on the app-side collector, not the actuator (proposal: BE-0020 boundary).
    assert base.Capability.NETWORK not in caps


# --- BE-0289: a stale handle re-resolves before failing ---------------------------------------------
# A `stale` reply means the screen re-snapshotted between resolve and actuate, so the handle went
# stale while the element is still present. The driver re-queries and re-actuates while the selector
# still resolves uniquely, and fails loudly the moment it does not — tolerating a snapshot race
# without ever absorbing a real disappearance. The fake transport is scripted per call so each of the
# four cases pins one half of that honest gate; `_driver` injects a no-op sleep, so the backoff is free.


def test_stale_handle_re_resolves_and_recovers_when_the_selector_still_resolves() -> None:
    # `stale` once, then `ok`: the button is present the whole time (a launch-time snapshot race), so
    # the re-resolved unique match re-actuates and the tap succeeds.
    actuations: list[dict[str, Any] | None] = []
    replies = iter(["stale", "ok"])

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK", traits=["button"]))
        actuations.append(body)
        return _Reply(status=next(replies))

    _driver(transport).tap({"id": "ok"})
    assert actuations == [{"handle": "h-ok"}, {"handle": "h-ok"}]  # re-actuated after re-resolving


def test_persistent_stale_exhausts_the_bound_then_fails_loudly() -> None:
    # The selector keeps resolving uniquely but every actuation is `stale`: the bound is spent and the
    # driver fails with the vanished-element error rather than retrying forever.
    actuations = 0

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        nonlocal actuations
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        actuations += 1
        return _Reply(status="stale")

    with pytest.raises(base.ElementNotFound, match="stale handle"):
        _driver(transport).tap({"id": "ok"})
    assert actuations == _STALE_MAX_ATTEMPTS  # bounded, not unbounded


def test_stale_then_gone_fails_immediately_as_element_not_found() -> None:
    # A `stale` whose re-query no longer resolves the selector is a genuine disappearance: fail at once
    # as ElementNotFound, spending no further actuation attempt (never absorb a real vanish, BE-0049).
    actuations = 0
    present = iter([True, False])  # resolves once (the first actuate), gone on the re-query

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        nonlocal actuations
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK")) if next(present) else _elements()
        actuations += 1
        return _Reply(status="stale")

    with pytest.raises(base.ElementNotFound):
        _driver(transport).tap({"id": "ok"})
    assert actuations == 1  # the re-query found nothing, so no second actuation was issued


def test_stale_then_ambiguous_fails_immediately_and_never_re_actuates() -> None:
    # A `stale` whose re-query resolves to many elements fails as AmbiguousSelector — the gate never
    # taps whatever happens to match (determinism first).
    actuations = 0
    first = iter([True, False])  # unique on the first resolve, ambiguous on the re-query

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        nonlocal actuations
        if path == "/elements":
            if next(first):
                return _elements(_el_wire("h1", "dup", "A", traits=["button"]))
            return _elements(
                _el_wire("h1", "dup", "A", traits=["button"]),
                _el_wire("h2", "dup", "B", traits=["button"]),
            )
        actuations += 1
        return _Reply(status="stale")

    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).tap({"id": "dup"})
    assert actuations == 1  # ambiguity is loud; no second actuation was issued


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


def test_health_ready_is_a_single_shot_probe_true_when_ready() -> None:
    # BE-0319 unit 3: one non-blocking probe (unlike await_ready's loop), so the cold-spawn liveness
    # wait owns the timing between probes.
    assert _driver(lambda m, p, b: _Reply(status="ready")).health_ready() is True


def test_health_ready_is_false_before_the_runner_is_up() -> None:
    assert _driver(lambda m, p, b: _Reply(status="starting")).health_ready() is False


def test_health_ready_swallows_a_transport_failure_as_not_ready() -> None:
    # A runner not yet accepting connections raises a transport failure; the single-shot probe reads
    # that as not-ready (never an error), so the caller keeps polling rather than aborting.
    def _refuse(m: str, p: str, b: Any) -> _Reply:
        raise _TransportFailure("refused", delivered=False)

    assert _driver(_refuse).health_ready() is False


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


def test_actuation_write_gets_a_longer_bounded_timeout_than_reads() -> None:
    # A multi-touch gesture on a loaded CI host can take longer than a read, and BE-0207 must not
    # re-issue a write after delivery (double-actuation risk) — so a write gets ONE longer window
    # rather than the retry a read leans on. Reads stay tight; the write window stays bounded so a
    # genuinely wedged runner still fails loudly.
    assert _timeout_for("GET") == _SOCKET_TIMEOUT_SECONDS
    assert _timeout_for("POST") == _ACTUATION_TIMEOUT_SECONDS
    assert _ACTUATION_TIMEOUT_SECONDS > _SOCKET_TIMEOUT_SECONDS
    assert _ACTUATION_TIMEOUT_SECONDS <= 60  # still bounded


def test_raw_transport_applies_the_per_method_socket_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The single-attempt transport must open each connection with the timeout for that method's
    # idempotency class: reads tight, actuation writes longer. Faked at the http.client boundary
    # (allowed: it is a real network call) so the wiring is verified without a Simulator.
    seen: list[tuple[str, float | None]] = []

    class _FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"status":"ok"}'

    class _FakeConn:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            self._timeout = timeout

        def connect(self) -> None:
            pass

        def request(self, method: str, path: str, body: Any = None, headers: Any = None) -> None:
            seen.append((method, self._timeout))

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr("bajutsu.drivers.xcuitest.http.client.HTTPConnection", _FakeConn)
    transport = _raw_http_transport("127.0.0.1", 1234)
    transport("GET", "/elements", None)
    transport("POST", "/gesture", {"kind": "pinch"})
    assert seen == [("GET", _SOCKET_TIMEOUT_SECONDS), ("POST", _ACTUATION_TIMEOUT_SECONDS)]


def test_raw_transport_splits_delivery_on_connect_versus_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The `delivered` flag drives whether a failed write may be re-issued, so it must flip exactly at
    # the socket opening: a connect failure never reached the runner (re-issuable), but any failure
    # once the socket is open may have (a POST is then not re-issued — a double-actuation risk).
    class _Conn:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            self.connected = False

        def connect(self) -> None:
            if fail_at == "connect":
                raise OSError("connection refused")
            self.connected = True

        def request(self, method: str, path: str, body: Any = None, headers: Any = None) -> None:
            raise OSError("broken pipe mid-send")

        def close(self) -> None:
            pass

    monkeypatch.setattr("bajutsu.drivers.xcuitest.http.client.HTTPConnection", _Conn)
    transport = _raw_http_transport("127.0.0.1", 1234)

    fail_at = "connect"
    with pytest.raises(_TransportFailure) as connect_exc:
        transport("POST", "/gesture", {"kind": "pinch"})
    assert connect_exc.value.delivered is False  # never reached the runner → safe to re-issue

    fail_at = "send"
    with pytest.raises(_TransportFailure) as send_exc:
        transport("POST", "/gesture", {"kind": "pinch"})
    assert (
        send_exc.value.delivered is True
    )  # bytes may have started reaching the runner → do not re-issue


# --- transient-transport retry policy (BE-0207) --- #
#
# The retry lives behind the `TransportFn` seam: `_with_retry` wraps a single-attempt transport and
# re-issues only *transport* failures (`_TransportFailure`), never a decoded outcome. It is exercised
# here with a fake inner transport (no Simulator), passing a no-op `sleep` so backoff adds no wall time.


def _counting(replies: list) -> tuple[TransportFn, list[int]]:
    """A fake inner transport that yields *replies* in order; each item is either a `_Reply` to return
    or an `Exception` (e.g. `_TransportFailure`, `XcuitestRunnerCrashError`) to raise. `calls[0]` counts
    how many times it was invoked."""
    calls = [0]

    def inner(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        calls[0] += 1
        item = replies.pop(0)
        if isinstance(item, Exception):
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


# --- mid-run crash recovery (BE-0287) --- #
#
# A crash outlives the BE-0207 transient budget: `_with_retry` exhausts and raises
# `XcuitestRunnerCrashError`, and `_with_crash_recovery` decides — by the same `delivered` split — to
# wait out the crash and re-issue an idempotent call, or to fail loudly on a write it must not re-send.
# Health-polling is faked (`health=lambda _t: ...`) so no wall time is spent waiting for a recovery.


def _crash(method: str, *, delivered: bool) -> XcuitestRunnerCrashError:
    return XcuitestRunnerCrashError(
        f"runner channel {method} /x failed: refused", method=method, delivered=delivered
    )


def test_exhausted_transient_retries_raise_a_crash_error_carrying_delivery_info() -> None:
    # The BE-0207 seam now signals exhaustion with the crash error, tagged so the recovery layer can
    # tell a safe-to-re-issue read from a write that must not be re-applied.
    inner, _calls = _counting([_TransportFailure("refused", delivered=False)] * _MAX_ATTEMPTS)
    with pytest.raises(XcuitestRunnerCrashError) as exc:
        _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert exc.value.method == "GET"
    assert exc.value.delivered is False
    # It is still an XcuitestChannelError, so callers that catch the broader type are unaffected.
    assert isinstance(exc.value, XcuitestChannelError)


def test_a_read_crash_waits_for_the_runner_then_re_issues() -> None:
    # A read is idempotent, so once the runner is back it is safe to re-issue and continue the run.
    inner, calls = _counting([_crash("GET", delivered=True), _Reply(status="ok")])
    reply = _with_crash_recovery(inner, health=lambda _t: True)("GET", "/elements", None)
    assert reply.status == "ok"
    assert calls[0] == 2  # crashed, then re-issued after the runner recovered


def test_an_undelivered_write_crash_re_issues_after_recovery() -> None:
    # A write that never reached the runner never applied, so re-issuing it after recovery is safe.
    inner, calls = _counting([_crash("POST", delivered=False), _Reply(status="ok")])
    reply = _with_crash_recovery(inner, health=lambda _t: True)(
        "POST", "/gesture", {"kind": "pinch"}
    )
    assert reply.status == "ok"
    assert calls[0] == 2


def test_a_delivered_write_crash_is_never_re_issued_and_fails_distinctly() -> None:
    # A delivered write may already have applied; re-sending could double-actuate. Even with the runner
    # recovered (health True), it must fail with a distinct crash diagnostic rather than re-issue.
    inner, calls = _counting([_crash("POST", delivered=True), _Reply(status="ok")])
    with pytest.raises(XcuitestRunnerCrashError, match="POST /gesture"):
        _with_crash_recovery(inner, health=lambda _t: True)("POST", "/gesture", {"kind": "pinch"})
    assert calls[0] == 1  # never re-issued


def test_a_read_crash_that_never_recovers_fails_loudly() -> None:
    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError, match="did not recover"):
        _with_crash_recovery(inner, health=lambda _t: False)("GET", "/elements", None)
    assert calls[0] == 1  # health never came back, so the read was not re-issued


def test_a_reissue_that_crashes_again_is_recovered_within_budget() -> None:
    # BE-0287: the observed flake crashes on back-to-back /screenshot calls — the runner comes back,
    # the re-issue crashes again, it comes back once more, then succeeds. The recovery must ride out
    # more than one consecutive crash (single-shot recovery would fail the run on the second crash).
    inner, calls = _counting(
        [_crash("GET", delivered=True), _crash("GET", delivered=True), _Reply(status="ok")]
    )
    reply = _with_crash_recovery(inner, health=lambda _t: True)("GET", "/screenshot", None)
    assert reply.status == "ok"
    assert calls[0] == 3  # crashed, recovered+re-issued, crashed again, recovered, then succeeded


def test_a_runner_that_never_stabilizes_fails_past_the_recovery_budget() -> None:
    # A runner that crashes on every re-issue is not a single flake; after `max_recoveries` consecutive
    # crashes the run fails loudly (distinct from the health-never-came-back "did not recover" path),
    # rather than looping forever.
    inner, calls = _counting([_crash("GET", delivered=False)] * 3)
    with pytest.raises(XcuitestRunnerCrashError, match="past the 2-recovery budget"):
        _with_crash_recovery(inner, health=lambda _t: True, max_recoveries=2)(
            "GET", "/elements", None
        )
    assert calls[0] == 3  # the initial call plus max_recoveries re-issues, all crashing


def test_a_normal_reply_passes_through_without_probing_health() -> None:
    def _boom(_t: float) -> bool:  # health must not be consulted on the happy path
        raise AssertionError("health probed on a non-crash call")

    inner, calls = _counting([_Reply(status="ok")])
    reply = _with_crash_recovery(inner, health=_boom)("GET", "/elements", None)
    assert reply.status == "ok"
    assert calls[0] == 1


def test_a_recovery_logs_the_crash_as_visibly_as_a_retried_blip(caplog: Any) -> None:
    # BE-0287 Unit 4: a crashed-and-recovered run must never be indistinguishable from one that never
    # crashed — both the crash and the recovery are logged.
    inner, _calls = _counting([_crash("GET", delivered=True), _Reply(status="ok")])
    with caplog.at_level("WARNING"):
        _with_crash_recovery(inner, health=lambda _t: True)("GET", "/elements", None)
    joined = " ".join(r.message for r in caplog.records).lower()
    assert "crash" in joined
    assert "recovered" in joined


def test_health_is_the_recovery_probe_so_it_never_recurses_into_recovery() -> None:
    # `/health` is how the layer detects recovery, so a crashed health probe must pass straight through
    # rather than trigger a nested recovery (which would block for the whole recovery timeout).
    def _boom(_t: float) -> bool:
        raise AssertionError("health probe triggered a nested recovery")

    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError):
        _with_crash_recovery(inner, health=_boom)("GET", "/health", None)
    assert calls[0] == 1


def test_recovery_timeout_is_bounded() -> None:
    # Long enough to outlast the ~30s outage the flake showed, short enough to fail loudly rather than
    # wait forever on a runner that is truly gone.
    assert 30 <= _RECOVERY_TIMEOUT_SECONDS <= 300


def test_await_health_returns_true_once_the_runner_is_ready() -> None:
    assert _await_health(lambda m, p, b: _Reply(status="ready"), timeout=1.0, sleep=lambda _s: None)


def test_await_health_returns_false_when_the_runner_never_becomes_ready() -> None:
    ticks = iter([0.0, 0.0, 0.5, 1.0, 1.0])  # deadline = 0.0 + 0.3; the third read is past it
    ok = _await_health(
        lambda m, p, b: _Reply(status="starting"),
        timeout=0.3,
        poll=0.0,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert ok is False


def test_await_health_treats_a_transport_failure_as_not_ready_then_recovers() -> None:
    inner, _calls = _counting(
        [_TransportFailure("refused", delivered=False), _Reply(status="ready")]
    )
    assert _await_health(inner, timeout=1.0, poll=0.0, sleep=lambda _s: None)


# --- handle_system_alert (BE-0316) ---------------------------------------------------------------
# The SpringBoard permission-prompt tap. Resolution stays Python-side over the buttons the runner
# returns from `/systemAlert/query`, so the same zero / ambiguous / index discipline every selector
# follows decides which button is tapped — proven here against a fake transport, no Simulator.


def _alert_transport(
    *button_batches: list[dict[str, Any]],
) -> tuple[TransportFn, list[tuple[str, str, dict[str, Any] | None]]]:
    """A transport whose `/systemAlert/query` yields each batch in turn (last repeats), recording taps.

    Successive batches let a test model the prompt appearing only on a later poll; a single batch
    (the common case) just answers every query with it.
    """
    sent: list[tuple[str, str, dict[str, Any] | None]] = []
    batches = iter(button_batches)
    current = next(batches, [])

    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        nonlocal current
        if path == "/systemAlert/query":
            batch = current
            current = next(batches, current)  # advance; the last batch repeats
            return _elements(*batch)
        sent.append((method, path, body))
        return _Reply(status="ok")

    return transport, sent


def test_handle_system_alert_resolves_the_labelled_button_and_taps_its_handle() -> None:
    transport, sent = _alert_transport(
        [
            _el_wire("h-allow", label="Allow", traits=["button"]),
            _el_wire("h-deny", label="Don't Allow", traits=["button"]),
        ]
    )
    _driver(transport).handle_system_alert({"label": "Allow"}, timeout=5.0)
    assert sent == [("POST", "/systemAlert/tap", {"handle": "h-allow"})]


def test_handle_system_alert_ambiguous_label_fails_without_index() -> None:
    transport, sent = _alert_transport(
        [
            _el_wire("h-a", label="OK", traits=["button"]),
            _el_wire("h-b", label="OK", traits=["button"]),
        ]
    )
    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).handle_system_alert({"label": "OK"}, timeout=5.0)
    assert sent == []  # no tap on an ambiguous match


def test_handle_system_alert_index_disambiguates_multiple_matches() -> None:
    transport, sent = _alert_transport(
        [
            _el_wire("h-a", label="OK", traits=["button"]),
            _el_wire("h-b", label="OK", traits=["button"]),
        ]
    )
    _driver(transport).handle_system_alert({"label": "OK", "index": 1}, timeout=5.0)
    assert sent == [("POST", "/systemAlert/tap", {"handle": "h-b"})]


def test_handle_system_alert_no_alert_within_timeout_fails() -> None:
    transport, sent = _alert_transport([])  # the prompt never appears
    with pytest.raises(base.ElementNotFound, match="no system alert appeared"):
        _driver(transport).handle_system_alert({"label": "Allow"}, timeout=0.0)
    assert sent == []


def test_handle_system_alert_waits_for_a_prompt_that_appears_on_a_later_poll() -> None:
    # Empty first, then the prompt — the condition wait rides the interval (no-op sleep) rather than
    # failing on the first empty read.
    transport, sent = _alert_transport([], [_el_wire("h-allow", label="Allow", traits=["button"])])
    _driver(transport).handle_system_alert({"label": "Allow"}, timeout=5.0)
    assert sent == [("POST", "/systemAlert/tap", {"handle": "h-allow"})]


def test_handle_system_alert_present_but_no_label_match_fails() -> None:
    transport, sent = _alert_transport([_el_wire("h-deny", label="Don't Allow", traits=["button"])])
    with pytest.raises(base.ElementNotFound):
        _driver(transport).handle_system_alert({"label": "Allow"}, timeout=0.0)
    assert sent == []


def test_handle_system_alert_reports_a_vanished_button_as_not_found() -> None:
    # The alert dismissed itself between query and tap: the tap reply is not "ok".
    def transport(method: str, path: str, body: dict[str, Any] | None) -> _Reply:
        if path == "/systemAlert/query":
            return _elements(_el_wire("h-allow", label="Allow", traits=["button"]))
        return _Reply(status="stale")

    with pytest.raises(base.ElementNotFound, match="vanished"):
        _driver(transport).handle_system_alert({"label": "Allow"}, timeout=5.0)

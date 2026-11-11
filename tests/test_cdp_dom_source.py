"""The Android WebView source over CDP (BE-0482): a recorded exchange, no device and no WebView."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from bajutsu.common.drivers import base
from bajutsu.common.drivers.dom import QUERY_JS
from bajutsu.common.drivers.webview import CdpDomSource, WebViewTransportError
from bajutsu.common.drivers.webview import cdp as cdp_mod
from bajutsu.common.drivers.webview.cdp import CdpConnection, connect_websocket
from bajutsu.common.drivers.webview.cdp_dom_source import fetch_targets, pick_target

Responder = Callable[[str, dict[str, Any]], dict[str, Any]]


class RecordedTransport:
    """Replays CDP: each sent command is answered by `responder`, after any queued event frames."""

    def __init__(self, responder: Responder, events: list[dict[str, Any]] | None = None) -> None:
        self.responder = responder
        self.sent: list[dict[str, Any]] = []
        self.pending: list[str] = [json.dumps(e) for e in events or []]
        self.closed = False
        self.dead = False

    def send(self, message: str) -> None:
        msg = json.loads(message)
        self.sent.append(msg)
        self.pending.append(
            json.dumps({"id": msg["id"], **self.responder(msg["method"], msg["params"])})
        )

    def recv(self, timeout: float) -> str:  # Transport shape
        if self.dead or not self.pending:
            raise WebViewTransportError("WebView DevTools socket closed: navigation")
        return self.pending.pop(0)

    def close(self) -> None:
        self.closed = True

    def methods(self) -> list[str]:
        return [m["method"] for m in self.sent]


def _value(v: Any) -> dict[str, Any]:
    return {"result": {"result": {"type": "object", "value": v}}}


def _target(
    x: float, y: float, w: float, h: float, *, visible: bool = True, n: int = 1
) -> dict[str, Any]:
    desc = {
        "attached": True,
        "visible": visible,
        "screenX": x,
        "screenY": y,
        "width": w,
        "height": h,
    }
    return {
        "type": "page",
        "description": json.dumps(desc),
        "webSocketDebuggerUrl": f"ws://localhost/devtools/page/{n}",
    }


# --- CdpConnection ---


def test_call_skips_events_and_stale_replies_until_its_own_id() -> None:
    t = RecordedTransport(
        lambda _m, _p: {"result": {"ok": 1}},
        events=[{"method": "Page.loadEventFired", "params": {}}, {"id": 99, "result": {}}],
    )
    assert CdpConnection(t).call("Page.enable") == {"ok": 1}


def test_call_raises_on_a_cdp_error() -> None:
    t = RecordedTransport(lambda _m, _p: {"error": {"code": -32000, "message": "nope"}})
    with pytest.raises(WebViewTransportError, match=r"CDP Page\.enable failed"):
        CdpConnection(t).call("Page.enable")


def test_call_raises_on_a_malformed_frame() -> None:
    class Garbage(RecordedTransport):
        def recv(self, timeout: float) -> str:
            return "not json"

    with pytest.raises(WebViewTransportError, match="malformed"):
        CdpConnection(Garbage(lambda _m, _p: {})).call("X")


def test_call_times_out_when_no_reply_carries_its_id() -> None:
    class Chatty(RecordedTransport):
        def recv(self, timeout: float) -> str:
            return json.dumps({"method": "Event"})

    with pytest.raises(WebViewTransportError, match="no reply"):
        CdpConnection(Chatty(lambda _m, _p: {}), timeout=0.01).call("X")


def test_evaluate_returns_by_value_and_raises_when_the_script_throws() -> None:
    t = RecordedTransport(lambda _m, _p: _value([1, 2]))
    assert CdpConnection(t).evaluate("[1, 2]") == [1, 2]
    assert t.sent[0]["params"]["returnByValue"] is True
    thrown = RecordedTransport(
        lambda _m, _p: {"result": {"exceptionDetails": {"exception": {"description": "Boom"}}}}
    )
    with pytest.raises(WebViewTransportError, match="Boom"):
        CdpConnection(thrown).evaluate("x")


# --- the WebSocket transport ---


class _FakeWs:
    def __init__(self, recv: Callable[[], Any]) -> None:
        self._recv = recv
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        if message == "fail":
            raise ConnectionResetError("reset")
        self.sent.append(message)

    def recv(self, timeout: float) -> Any:
        return self._recv()

    def close(self) -> None:
        raise RuntimeError("already closed")


def test_websocket_transport_maps_every_failure_to_one_error_type() -> None:
    t = cdp_mod._WebSocketTransport(_FakeWs(lambda: b"bytes frame"))
    assert t.recv(1.0) == "bytes frame"
    t.send("ok")
    with pytest.raises(WebViewTransportError, match="send failed"):
        t.send("fail")
    t.close()  # a dead socket closes quietly

    def timeout() -> Any:
        raise TimeoutError

    with pytest.raises(WebViewTransportError, match="no reply"):
        cdp_mod._WebSocketTransport(_FakeWs(timeout)).recv(0.5)

    def closed() -> Any:
        raise EOFError("closed by navigation")

    with pytest.raises(WebViewTransportError, match="closed"):
        cdp_mod._WebSocketTransport(_FakeWs(closed)).recv(0.5)


def test_connect_names_the_install_command_when_websockets_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "websockets.sync.client", None)
    with pytest.raises(WebViewTransportError, match=r"bajutsu\[android\]"):
        connect_websocket("ws://127.0.0.1:1/devtools/page/1", 0.1)


def test_connect_reports_a_refused_socket() -> None:
    pytest.importorskip("websockets")
    with pytest.raises(WebViewTransportError, match="could not open"):
        connect_websocket("ws://127.0.0.1:1/devtools/page/1", 0.5)


# --- target selection by rectangle ---


def test_pick_target_matches_a_clipped_webview_by_containment() -> None:
    # The adb tree clips the WebView under a toolbar; CDP reports its full rectangle.
    full = _target(0, 100, 1080, 2000)
    assert pick_target([(5000, full)], (0, 220, 1080, 1700)) == (5000, full)


def test_pick_target_skips_hidden_detached_and_malformed_pages() -> None:
    hidden = _target(0, 0, 1080, 2400, visible=False)
    detached = {"description": json.dumps({"attached": False, "visible": True})}
    malformed = {"description": "not json"}
    missing = {"description": json.dumps({"attached": True, "visible": True})}
    with pytest.raises(base.ElementNotFound):
        pick_target([(1, hidden), (1, detached), (1, malformed), (1, missing)], (0, 0, 10, 10))


def test_pick_target_fails_an_overlapping_pair_as_ambiguous() -> None:
    big, small = _target(0, 0, 1080, 2400, n=1), _target(100, 100, 400, 400, n=2)
    with pytest.raises(base.AmbiguousSelector, match="overlapping"):
        pick_target([(1, big), (1, small)], (150, 150, 100, 100))


# --- fetch_targets over a real loopback HTTP server ---


def _serve(body: bytes) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    return server


def test_fetch_targets_reads_json_list_and_rejects_a_non_list() -> None:
    server = _serve(json.dumps([_target(0, 0, 1, 1), "junk"]).encode())
    try:
        assert len(fetch_targets(server.server_address[1], 2.0)) == 1
    finally:
        server.shutdown()
    server = _serve(b'{"not": "a list"}')
    try:
        with pytest.raises(WebViewTransportError, match="not a list"):
            fetch_targets(server.server_address[1], 2.0)
    finally:
        server.shutdown()
    with pytest.raises(WebViewTransportError, match="could not read"):
        fetch_targets(1, 0.5)


# --- CdpDomSource, one session ---

_PS = "  PID NAME\n 4242 com.example.app\n"
_UNIX = "0000: 00000002 0 00010000 0001 01 1 @webview_devtools_remote_4242\n"


class _Adb:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if "ps" in argv:
            return _PS
        if "/proc/net/unix" in argv:
            return _UNIX
        if argv[3:4] == ["forward"] and "--remove" not in argv:
            return "51234\n"
        return ""


def _source(
    transport: RecordedTransport, targets: list[dict[str, Any]] | None = None
) -> tuple[CdpDomSource, _Adb, list[str]]:
    run = _Adb()
    urls: list[str] = []

    def connect(url: str, _timeout: float) -> RecordedTransport:
        urls.append(url)
        return transport

    src = CdpDomSource(
        "emulator-5554",
        "com.example.app",
        run,
        fetch=lambda _port, _timeout: (
            targets if targets is not None else [_target(0, 0, 1080, 2400)]
        ),
        connect=connect,
    )
    return src, run, urls


def test_session_connects_to_the_chosen_page_through_the_forwarded_port_and_cleans_up() -> None:
    t = RecordedTransport(lambda _m, _p: _value([]))
    src, run, urls = _source(t)
    with src.session("web", (0, 200, 1080, 1800)):
        pass
    assert urls == ["ws://127.0.0.1:51234/devtools/page/1"]
    assert t.closed
    assert run.calls[-1] == ["adb", "-s", "emulator-5554", "forward", "--remove", "tcp:51234"]


def test_session_removes_the_forward_when_no_page_matches() -> None:
    src, run, _ = _source(RecordedTransport(lambda _m, _p: {}), targets=[])
    with pytest.raises(base.ElementNotFound), src.session("web", (0, 0, 10, 10)):
        pass
    assert run.calls[-1][3:5] == ["forward", "--remove"]


def test_session_rejects_a_page_with_no_debugger_url() -> None:
    page = _target(0, 0, 1080, 2400)
    del page["webSocketDebuggerUrl"]
    src, _, _ = _source(RecordedTransport(lambda _m, _p: {}), targets=[page])
    with (
        pytest.raises(WebViewTransportError, match="debugger URL"),
        src.session("web", (0, 0, 1, 1)),
    ):
        pass


def test_query_dom_runs_the_shared_query_script_and_parses_its_records() -> None:
    rec = {"identifier": "go", "role": "button", "label": "Go", "frame": [1, 2, 3, 4]}
    t = RecordedTransport(lambda _m, _p: _value([rec]))
    src, _, _ = _source(t)
    with src.session("web", (0, 0, 10, 10)):
        els = src.query_dom("web")
    assert els[0]["identifier"] == "go"
    assert t.sent[0]["params"]["expression"] == f"({QUERY_JS})()"


def test_query_dom_rejects_a_non_list_result() -> None:
    src, _, _ = _source(RecordedTransport(lambda _m, _p: _value(None)))
    with (
        src.session("web", (0, 0, 1, 1)),
        pytest.raises(WebViewTransportError, match="no element list"),
    ):
        src.query_dom("web")


def test_tap_hit_tests_then_sends_a_touch_start_and_end() -> None:
    def responder(method: str, _p: dict[str, Any]) -> dict[str, Any]:
        return _value(True) if method == "Runtime.evaluate" else {"result": {}}

    t = RecordedTransport(responder)
    src, _, _ = _source(t)
    with src.session("web", (0, 0, 1, 1)):
        src.tap_element("web", (10.5, 20.0))
    assert t.methods() == [
        "Runtime.evaluate",
        "Input.dispatchTouchEvent",
        "Input.dispatchTouchEvent",
    ]
    assert "elementFromPoint(10.5, 20.0)" in t.sent[0]["params"]["expression"]
    assert t.sent[1]["params"] == {"type": "touchStart", "touchPoints": [{"x": 10.5, "y": 20.0}]}
    assert t.sent[2]["params"]["type"] == "touchEnd"


def test_tap_at_a_point_with_no_element_fails_before_any_touch() -> None:
    t = RecordedTransport(lambda _m, _p: _value(False))
    src, _, _ = _source(t)
    with src.session("web", (0, 0, 1, 1)), pytest.raises(base.ElementNotTappable):
        src.tap_element("web", (5000, 5000))
    assert "Input.dispatchTouchEvent" not in t.methods()


def test_type_moves_the_caret_inserts_and_fires_change() -> None:
    t = RecordedTransport(lambda m, _p: _value(True) if m == "Runtime.evaluate" else {"result": {}})
    src, _, _ = _source(t)
    with src.session("web", (0, 0, 1, 1)):
        src.type_text("web", "héllo")
    assert t.methods() == ["Runtime.evaluate", "Input.insertText", "Runtime.evaluate"]
    assert "setSelectionRange" in t.sent[0]["params"]["expression"]
    assert t.sent[1]["params"] == {"text": "héllo"}
    assert "'change'" in t.sent[2]["params"]["expression"]


def test_type_with_no_focused_field_fails() -> None:
    t = RecordedTransport(lambda _m, _p: _value(False))
    src, _, _ = _source(t)
    with (
        src.session("web", (0, 0, 1, 1)),
        pytest.raises(WebViewTransportError, match="no focused field"),
    ):
        src.type_text("web", "x")
    assert "Input.insertText" not in t.methods()


def test_scroll_to_quotes_the_test_id_into_the_selector() -> None:
    t = RecordedTransport(lambda _m, _p: _value(None))
    src, _, _ = _source(t)
    with src.session("web", (0, 0, 1, 1)):
        src.scroll_to("web", 'a"b')
    expr = t.sent[0]["params"]["expression"]
    assert json.dumps('[data-testid="a\\"b"]') in expr
    assert "scrollIntoView" in expr


def test_a_socket_closed_mid_block_fails_the_next_call_and_still_cleans_up() -> None:
    t = RecordedTransport(lambda _m, _p: _value([]))
    src, run, _ = _source(t)
    with src.session("web", (0, 0, 1, 1)):
        src.query_dom("web")
        t.dead = True
        with pytest.raises(WebViewTransportError, match="closed"):
            src.query_dom("web")
    assert t.closed
    assert run.calls[-1][3:5] == ["forward", "--remove"]


def test_a_call_outside_a_session_fails() -> None:
    src, _, _ = _source(RecordedTransport(lambda _m, _p: {}))
    with pytest.raises(WebViewTransportError, match="outside"):
        src.query_dom("web")


# --- review hardening: malformed replies, empty frames, typing guard, several sockets ---


def test_a_reply_with_no_result_object_is_a_transport_error() -> None:
    t = RecordedTransport(lambda _m, _p: {"result": "not an object"})
    with pytest.raises(WebViewTransportError, match="no result object"):
        CdpConnection(t).call("X")


def test_a_malformed_exception_detail_still_fails_as_a_script_error() -> None:
    t = RecordedTransport(
        lambda _m, _p: {"result": {"exceptionDetails": {"exception": "?", "text": "Uncaught"}}}
    )
    with pytest.raises(WebViewTransportError, match="Uncaught"):
        CdpConnection(t).evaluate("x")


def test_pick_target_refuses_an_empty_within_frame() -> None:
    with pytest.raises(base.ElementNotFound, match="empty frame"):
        pick_target([(1, _target(0, 0, 1080, 2400))], (0, 0, 0, 0))


def test_tap_hit_test_rejects_the_page_body_and_root() -> None:
    t = RecordedTransport(lambda _m, _p: _value(True))
    src, _, _ = _source(t)
    with src.session("web", (0, 0, 1, 1)):
        src.tap_element("web", (1, 1))
    hit = t.sent[0]["params"]["expression"]
    assert "document.body" in hit and "document.documentElement" in hit


def test_type_counts_only_a_text_entry_field_as_focused() -> None:
    t = RecordedTransport(lambda m, _p: _value(True) if m == "Runtime.evaluate" else {"result": {}})
    src, _, _ = _source(t)
    with src.session("web", (0, 0, 1, 1)):
        src.type_text("web", "x")
    guard = t.sent[0]["params"]["expression"]
    assert "HTMLTextAreaElement" in guard and "'button'" in guard


class _TwoSocketAdb(_Adb):
    """Two WebView processes, each forwarded on its own host port."""

    def __init__(self) -> None:
        super().__init__()
        self._ports = iter(["40001\n", "40002\n"])

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if "ps" in argv:
            return "  PID NAME\n 10 com.example.app\n 11 com.example.app:web\n"
        if "/proc/net/unix" in argv:
            return "0: 1 @webview_devtools_remote_10\n0: 1 @webview_devtools_remote_11\n"
        if argv[3:4] == ["forward"] and "--remove" not in argv:
            return next(self._ports)
        return ""

    def removed(self) -> list[str]:
        return [c[-1] for c in self.calls if "--remove" in c]


def _two_socket_source(
    fetch: Callable[[int, float], list[dict[str, Any]]],
) -> tuple[CdpDomSource, _TwoSocketAdb]:
    run = _TwoSocketAdb()
    src = CdpDomSource(
        "emulator-5554",
        "com.example.app",
        run,
        fetch=fetch,
        connect=lambda _url, _t: RecordedTransport(lambda _m, _p: _value([])),
    )
    return src, run


def test_containment_spans_every_socket_and_both_forwards_are_removed() -> None:
    # Each process serves a page that contains the frame: ambiguous, not "the first socket wins".
    src, run = _two_socket_source(lambda port, _t: [_target(0, 0, 1080, 2400, n=port)])
    with pytest.raises(base.AmbiguousSelector), src.session("web", (0, 100, 500, 500)):
        pass
    assert run.removed() == ["tcp:40001", "tcp:40002"]


def test_a_failed_fetch_on_the_second_socket_still_removes_the_first_forward() -> None:
    def fetch(port: int, _t: float) -> list[dict[str, Any]]:
        if port == 40002:
            raise WebViewTransportError("could not read WebView targets")
        return [_target(0, 0, 1080, 2400)]

    src, run = _two_socket_source(fetch)
    with pytest.raises(WebViewTransportError), src.session("web", (0, 100, 500, 500)):
        pass
    assert run.removed() == ["tcp:40001", "tcp:40002"]

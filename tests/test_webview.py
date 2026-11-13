"""Tests for the WebView bridge client (Python → BajutsuKit HTTP)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from bajutsu.common.drivers import base
from bajutsu.common.drivers.webview import WebViewBridge, WebViewTransportError


def _fake_bridge_server(
    dom_records: list[dict[str, Any]] | None,
    tap_ok: bool = True,
    tap_status: str | None = None,
) -> ThreadingHTTPServer:
    """A fake BajutsuKit bridge server returning canned responses."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/webview/dom"):
                body = json.dumps({"elements": dom_records}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if self.path == "/webview/tap":
                status = tap_status or ("ok" if tap_ok else "error")
                body = json.dumps({"status": status}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path in ("/webview/type", "/webview/scroll"):
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    return server


def test_query_dom_returns_normalized_elements() -> None:
    records = [
        {
            "identifier": "place-order",
            "role": "button",
            "label": "Place Order",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [10, 20, 100, 40],
        },
        {
            "identifier": "order-total",
            "role": "div",
            "label": "$42.00",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [10, 80, 100, 20],
        },
    ]
    server = _fake_bridge_server(records)
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        elements = bridge.query_dom("checkout.webview")
        assert len(elements) == 2
        assert elements[0]["identifier"] == "place-order"
        assert base.Trait.BUTTON in elements[0]["traits"]
        assert elements[1]["identifier"] == "order-total"
    finally:
        server.shutdown()
        server.server_close()


def test_query_dom_empty_webview() -> None:
    server = _fake_bridge_server([])
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        elements = bridge.query_dom("empty.webview")
        assert elements == []
    finally:
        server.shutdown()
        server.server_close()


def test_tap_element_succeeds() -> None:
    server = _fake_bridge_server([], tap_ok=True)
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        bridge.tap_element("checkout.webview", (50.0, 30.0))
    finally:
        server.shutdown()
        server.server_close()


def test_tap_element_failure_raises() -> None:
    server = _fake_bridge_server([], tap_ok=False)
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        with pytest.raises(WebViewTransportError, match="tap"):
            bridge.tap_element("checkout.webview", (50.0, 30.0))
    finally:
        server.shutdown()
        server.server_close()


def test_tap_at_a_point_with_no_element_is_not_tappable() -> None:
    # The same refusal the Android source gives, so a portable scenario fails alike on both.
    server = _fake_bridge_server([], tap_status="not-found")
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        with pytest.raises(base.ElementNotTappable):
            bridge.tap_element("wv", (5000.0, 5000.0))
    finally:
        server.shutdown()
        server.server_close()


def test_query_dom_rejects_a_null_element_list() -> None:
    server = _fake_bridge_server(None)
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        with pytest.raises(WebViewTransportError, match="no element list"):
            bridge.query_dom("wv")
    finally:
        server.shutdown()
        server.server_close()


def test_bridge_reports_an_error_status_with_its_body() -> None:
    bridge_404 = _fake_bridge_server([])
    try:
        bridge = WebViewBridge(port=bridge_404.server_address[1])
        with pytest.raises(WebViewTransportError, match="HTTP 404"):
            bridge._call("/nope", None)  # the one path the fake answers 404
    finally:
        bridge_404.shutdown()
        bridge_404.server_close()


def test_query_dom_names_a_webview_id_the_app_does_not_carry() -> None:
    # BajutsuKit answers 404 when no WKWebView carries the id, so a `within` naming the wrong id
    # fails on that id rather than reading as an empty page and failing later as "not found".
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(
                {
                    "status": "error",
                    "message": "no WKWebView with accessibilityIdentifier 'webview.host'",
                }
            ).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        with pytest.raises(
            WebViewTransportError, match=r"HTTP 404.*accessibilityIdentifier .*webview\.host"
        ):
            bridge.query_dom("webview.host")
    finally:
        server.shutdown()
        server.server_close()


def test_type_text_succeeds() -> None:
    server = _fake_bridge_server([])
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        bridge.type_text("wv", "hello")
    finally:
        server.shutdown()
        server.server_close()


def test_scroll_to_succeeds() -> None:
    server = _fake_bridge_server([])
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        bridge.scroll_to("wv", "some-element")
    finally:
        server.shutdown()
        server.server_close()


def test_bridge_connection_refused() -> None:
    bridge = WebViewBridge(port=1)
    with pytest.raises(WebViewTransportError, match="unreachable"):
        bridge.query_dom("any.webview")


def test_bridge_maps_a_reply_cut_off_mid_request_to_the_transport_error() -> None:
    # An app that dies while it handles the request (the BajutsuKit scroll crash this guards against)
    # closes the socket with no reply: `RemoteDisconnected`, or a reset when the body went unread —
    # neither is a `URLError`, and either would otherwise escape the run loop.
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.close_connection = True

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    try:
        bridge = WebViewBridge(port=server.server_address[1])
        with pytest.raises(WebViewTransportError, match="unreachable"):
            bridge.scroll_to("wv", "web.name")
    finally:
        server.shutdown()
        server.server_close()


def test_bridge_session_is_a_no_op_span() -> None:
    # The in-app server finds the WebView by id on every request, so a block opens nothing (BE-0482).
    bridge = WebViewBridge(port=1)
    with bridge.session("wv", (0.0, 0.0, 10.0, 10.0)) as entered:
        assert entered is None

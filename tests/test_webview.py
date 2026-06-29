"""Tests for the WebView bridge client (Python → BajutsuKit HTTP)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from bajutsu.drivers import base
from bajutsu.webview import WebViewBridge


def _fake_bridge_server(
    dom_records: list[dict[str, Any]],
    tap_ok: bool = True,
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
                status = "ok" if tap_ok else "error"
                body = json.dumps({"status": status}).encode()
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
        with pytest.raises(RuntimeError, match="tap"):
            bridge.tap_element("checkout.webview", (50.0, 30.0))
    finally:
        server.shutdown()
        server.server_close()


def test_bridge_connection_refused() -> None:
    bridge = WebViewBridge(port=1)
    with pytest.raises(ConnectionError):
        bridge.query_dom("any.webview")

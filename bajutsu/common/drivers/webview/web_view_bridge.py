"""The HTTP client for the BajutsuKit WebView bridge server."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from contextlib import AbstractContextManager, nullcontext

from bajutsu.common.drivers.base import Element, Frame, Point
from bajutsu.common.drivers.dom import parse_dom


class WebViewBridge:
    """HTTP client for the BajutsuKit WebView bridge server."""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.port = (
            port  # the host port this bridge reserved — one per lease, so leases never collide
        )
        self._base_url = f"http://{host}:{port}"

    def session(self, webview_id: str, frame: Frame) -> AbstractContextManager[None]:  # noqa: ARG002  # DomSource shape
        """Nothing to open: the in-app server finds the WebView by its id on every request."""
        return nullcontext()

    def query_dom(self, webview_id: str) -> list[Element]:
        """Query the DOM of the WebView identified by its native accessibility id."""
        url = f"{self._base_url}/webview/dom?id={urllib.parse.quote(webview_id)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ConnectionError(f"WebView bridge unreachable at {self._base_url}: {e}") from e
        return parse_dom(data.get("elements", []))

    def tap_element(self, webview_id: str, point: Point) -> None:
        """Tap a point inside the WebView's coordinate space."""
        payload = json.dumps({"id": webview_id, "point": [point[0], point[1]]}).encode()
        req = urllib.request.Request(  # noqa: S310
            f"{self._base_url}/webview/tap",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ConnectionError(f"WebView bridge unreachable at {self._base_url}: {e}") from e
        if data.get("status") != "ok":
            raise RuntimeError(f"WebView tap failed: {data}")

    def type_text(self, webview_id: str, text: str) -> None:
        """Type text into the currently focused element inside the WebView."""
        payload = json.dumps({"id": webview_id, "text": text}).encode()
        req = urllib.request.Request(  # noqa: S310
            f"{self._base_url}/webview/type",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ConnectionError(f"WebView bridge unreachable at {self._base_url}: {e}") from e
        if data.get("status") != "ok":
            raise RuntimeError(f"WebView type failed: {data}")

    def scroll_to(self, webview_id: str, element_id: str) -> None:
        """Scroll the element with the given data-testid into view."""
        payload = json.dumps({"id": webview_id, "elementId": element_id}).encode()
        req = urllib.request.Request(  # noqa: S310
            f"{self._base_url}/webview/scroll",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ConnectionError(f"WebView bridge unreachable at {self._base_url}: {e}") from e
        if data.get("status") not in ("ok", "not-found"):
            raise RuntimeError(f"WebView scroll failed: {data}")

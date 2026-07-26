"""WebView bridge client — Python side of the BajutsuKit WebView channel.

Sends HTTP requests to the BajutsuKit bridge server running inside the app under test.
The server exposes the WebView's DOM as normalized elements and dispatches tap actions.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from bajutsu.dom import parse_dom
from bajutsu.drivers.base import (
    Capability,
    Element,
    Point,
    Selector,
    UnsupportedAction,
    find_all,
    resolve_unique,
)


class WebViewBridge:
    """HTTP client for the BajutsuKit WebView bridge server."""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self._base_url = f"http://{host}:{port}"

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


class DomSource(Protocol):
    """Minimal bridge interface — satisfied by WebViewBridge and test fakes."""

    def query_dom(self, webview_id: str) -> list[Element]: ...
    def tap_element(self, webview_id: str, point: Point) -> None: ...
    def type_text(self, webview_id: str, text: str) -> None: ...
    def scroll_to(self, webview_id: str, element_id: str) -> None: ...


class WebContextDriver:
    """Driver wrapper that resolves selectors against a WebView's DOM instead of the native tree.

    Created by the run loop when entering a ``web`` block; delegates query/tap to the bridge and
    rejects actions the first slice does not support (swipe, type, pinch, rotate).
    """

    name = "webview"

    def __init__(self, bridge: DomSource, webview_id: str) -> None:
        self._bridge = bridge
        self._webview_id = webview_id

    def query(self) -> list[Element]:
        return self._bridge.query_dom(self._webview_id)

    def _center(self, sel: Selector) -> Point:
        el = resolve_unique(self.query(), sel)
        x, y, w, h = el["frame"]
        return (x + w / 2, y + h / 2)

    def tap(self, sel: Selector) -> None:
        el = resolve_unique(self.query(), sel)
        eid = el.get("identifier")
        if eid:
            self._bridge.scroll_to(self._webview_id, eid)
        x, y, w, h = el["frame"]
        self._bridge.tap_element(self._webview_id, (x + w / 2, y + h / 2))

    def tap_point(self, p: Point) -> None:
        self._bridge.tap_element(self._webview_id, p)

    def double_tap(self, sel: Selector) -> None:
        point = self._center(sel)
        self._bridge.tap_element(self._webview_id, point)

    def long_press(self, sel: Selector, duration: float) -> None:
        raise UnsupportedAction("long_press is not supported in web context (first slice)")

    def swipe(self, frm: Point, to: Point) -> None:
        raise UnsupportedAction("swipe is not supported in web context (first slice)")

    def scroll(self, frm: Point, to: Point) -> None:
        raise UnsupportedAction("scroll is not supported in web context (first slice)")

    def back(self) -> None:
        raise UnsupportedAction("back is not supported in web context (first slice)")

    def pinch(self, sel: Selector, scale: float) -> None:
        raise UnsupportedAction("pinch is not supported in web context (first slice)")

    def rotate(self, sel: Selector, radians: float) -> None:
        raise UnsupportedAction("rotate is not supported in web context (first slice)")

    def type_text(self, text: str) -> None:
        self._bridge.type_text(self._webview_id, text)

    def delete_text(self, count: int) -> None:
        raise UnsupportedAction("delete/clear is not supported in web context (first slice)")

    def select_all(self) -> None:
        raise UnsupportedAction("select is not supported in web context (first slice)")

    def copy_selection(self) -> None:
        raise UnsupportedAction("copy is not supported in web context (first slice)")

    def select_option(self, sel: Selector, option: str) -> None:
        raise UnsupportedAction("selectOption is not supported in web context (first slice)")

    def handle_system_alert(self, sel: Selector, timeout: float) -> None:
        # BE-0316 taps an iOS SpringBoard prompt; a WebView DOM context has no OS-level alert, and
        # only the resident-runner XCUITest backend declares the capability, so this never runs.
        raise UnsupportedAction("handleSystemAlert is iOS-only; not supported in web context")

    def system_alert_labels(self) -> list[str]:
        # A WebView DOM context sees no SpringBoard alert layer; the reactive native path never runs.
        return []

    def wait_for(self, sel: Selector) -> bool:
        """Single-shot: whether `sel` matches the WebView's current DOM (BE-0118).

        The deadline poll lives in the shared `base.wait_until`, so the timeout is honoured
        identically on every backend.
        """
        return bool(find_all(self.query(), sel))

    def screenshot(self, path: str) -> None:
        raise UnsupportedAction("screenshot is not supported in web context (first slice)")

    def capabilities(self) -> set[str]:
        return {Capability.QUERY, Capability.WEBVIEW}

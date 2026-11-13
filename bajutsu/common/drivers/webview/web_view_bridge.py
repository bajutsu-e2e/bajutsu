"""The HTTP client for the BajutsuKit WebView bridge server."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from bajutsu.common.drivers.base import Element, ElementNotTappable, Frame, Point
from bajutsu.common.drivers.dom import parse_dom

from .cdp import WebViewTransportError


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
        data = self._call(f"/webview/dom?id={urllib.parse.quote(webview_id)}", None)
        records = data.get("elements")
        if not isinstance(records, list):
            raise WebViewTransportError(f"the WebView bridge returned no element list: {data}")
        return parse_dom(records)

    def tap_element(self, webview_id: str, point: Point) -> None:
        """Tap a point inside the WebView's coordinate space."""
        data = self._call("/webview/tap", {"id": webview_id, "point": [point[0], point[1]]})
        if data.get("status") == "not-found":
            # The bridge answers `not-found` both when `elementFromPoint` resolves nothing (the same
            # refusal the Android source gives) and when it finds no WKWebView with this id at all,
            # so the reason names both.
            raise ElementNotTappable(
                f"web: no element at {point} in the WebView {webview_id!r}, or the app has no"
                " WKWebView carrying that accessibility identifier"
            )
        if data.get("status") != "ok":
            raise WebViewTransportError(f"WebView tap failed: {data}")

    def type_text(self, webview_id: str, text: str) -> None:
        """Type text into the currently focused element inside the WebView."""
        data = self._call("/webview/type", {"id": webview_id, "text": text})
        if data.get("status") != "ok":
            raise WebViewTransportError(f"WebView type failed: {data}")

    def scroll_to(self, webview_id: str, element_id: str) -> None:
        """Scroll the element with the given data-testid into view."""
        data = self._call("/webview/scroll", {"id": webview_id, "elementId": element_id})
        if data.get("status") not in ("ok", "not-found"):
            raise WebViewTransportError(f"WebView scroll failed: {data}")

    def _call(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        # Every failure becomes the one type `_handle_web` turns into the block's reason, as the
        # Android source does (BE-0482): an app that dies mid-request closes the socket with no reply
        # (`RemoteDisconnected`, an `OSError` and not a `URLError`), and an escaping exception would
        # discard every scenario's result in the run.
        req = urllib.request.Request(  # noqa: S310
            f"{self._base_url}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                body = resp.read()
        except urllib.error.HTTPError as e:
            # The bridge answered, with an error status: its body says why, not "unreachable".
            raise WebViewTransportError(
                f"WebView bridge answered {path} with HTTP {e.code}: {e.read()[:200]!r}"
            ) from e
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            raise WebViewTransportError(
                f"WebView bridge unreachable at {self._base_url}: {type(e).__name__}: {e}"
            ) from e
        try:
            data = json.loads(body)
        except ValueError as e:
            # The bridge answered, with a body that is not JSON (an empty or truncated write): a broken
            # reply, not an unreachable bridge.
            raise WebViewTransportError(
                f"WebView bridge answered {path} with a body that is not JSON: {body[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise WebViewTransportError(
                f"WebView bridge answered {path} with a non-object: {data!r}"
            )
        return data

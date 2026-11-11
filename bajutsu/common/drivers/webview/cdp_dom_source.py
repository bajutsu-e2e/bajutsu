"""The Android `DomSource`: reach an embedded WebView over the Chrome DevTools Protocol (BE-0482).

Android ships its own channel into a debuggable WebView — the `webview_devtools_remote_<pid>`
socket — so, unlike iOS, no app-side server is needed. Each `web:` block opens one `session`: it
forwards the app's sockets, picks the one page whose on-screen rectangle contains the `within`
element's frame, and holds a WebSocket to that page until the block ends.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from bajutsu.common.backend_cli import adb
from bajutsu.common.drivers.base import (
    AmbiguousSelector,
    Element,
    ElementNotFound,
    ElementNotTappable,
    Frame,
    Point,
)
from bajutsu.common.drivers.dom import QUERY_JS, parse_dom

from . import cdp_discovery
from .cdp import CdpConnection, Transport, WebViewTransportError, connect_websocket

FetchTargets = Callable[[int, float], list[dict[str, Any]]]
Connect = Callable[[str, float], Transport]

# A target is one candidate page: the host port its socket is forwarded on, and its `/json/list` row.
_Candidate = tuple[int, dict[str, Any]]

# The iOS bridge's own scripts (BajutsuKit `BajutsuWebView`), so one portable scenario leaves the same
# text and fires the same events on both platforms. `Input.insertText` inserts at the caret and fires
# `input` alone, so the caret goes to the end first (iOS appends) and `change` is fired after.
# Only a field that takes typed text counts as focused: `'value' in el` alone also admits a button,
# a <select>, or an <li>, and a date or time picker input is no plain-text editable either; in each,
# `Input.insertText` inserts nothing and the step would pass anyway.
_CARET_TO_END_JS = """
(() => {
  const el = document.activeElement;
  const noText = ['button', 'checkbox', 'color', 'date', 'datetime-local', 'file', 'hidden',
                  'image', 'month', 'radio', 'range', 'reset', 'submit', 'time', 'week'];
  const takesText = el instanceof HTMLTextAreaElement
    || (el instanceof HTMLInputElement && !noText.includes(el.type));
  if (!takesText) return false;
  const end = String(el.value).length;
  try { el.setSelectionRange(end, end); } catch (e) { /* a type with no selection, e.g. number */ }
  return true;
})()
"""
_FIRE_CHANGE_JS = """
(() => {
  const el = document.activeElement;
  if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
})()
"""


def _scroll_js(element_id: str) -> str:
    sel = json.dumps(f"[data-testid={json.dumps(element_id)}]")
    return (
        f"(() => {{ const el = document.querySelector({sel});"
        " if (el) el.scrollIntoView({ behavior: 'instant', block: 'center' }); })()"
    )


def _rect(target: dict[str, Any]) -> Frame | None:
    """The page's on-screen rectangle in device pixels, or None when it is detached or hidden."""
    try:
        desc = json.loads(target.get("description") or "")
    except ValueError:
        return None
    if not isinstance(desc, dict) or desc.get("attached") is not True:
        return None
    if desc.get("visible") is not True:
        return None
    try:
        return (
            float(desc["screenX"]),
            float(desc["screenY"]),
            float(desc["width"]),
            float(desc["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _contains(outer: Frame, inner: Frame) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh


def pick_target(candidates: list[_Candidate], frame: Frame) -> _Candidate:
    """The one attached, visible page whose rectangle contains the native element's `frame`.

    Containment rather than equality: the adb tree clips a view's bounds to the screen and to every
    ancestor, while CDP reports the WebView's full rectangle, so a partly hidden WebView would never
    match an equal rectangle. Two overlapping WebViews both contain the frame and fail as ambiguous —
    determinism over a guess (prime directive 2).

    Raises:
        ElementNotFound: no page contains the frame.
        AmbiguousSelector: two or more pages contain it.
    """
    if frame[2] <= 0 or frame[3] <= 0:
        # An empty rectangle — the element clipped away or offscreen — sits inside any page that
        # covers its corner, which would pick a WebView the author never meant.
        raise ElementNotFound(f"web: the within element has an empty frame {frame} (offscreen?)")
    shown = [(port, t, r) for port, t in candidates if (r := _rect(t)) is not None]
    matches = [(port, t) for port, t, r in shown if _contains(r, frame)]
    if not matches:
        raise ElementNotFound(
            f"web: no WebView page contains the within frame {frame} "
            f"({len(shown)} attached and visible of {len(candidates)} listed: "
            f"{[r for _, _, r in shown]})"
        )
    if len(matches) > 1:
        raise AmbiguousSelector(
            f"web: {len(matches)} WebView pages contain the within frame {frame}; "
            "overlapping WebViews cannot be told apart"
        )
    return matches[0]


def fetch_targets(port: int, timeout: float) -> list[dict[str, Any]]:
    """Read the DevTools `/json/list` served on the forwarded host `port`.

    Raises:
        WebViewTransportError: the endpoint is unreachable or answers with something not a list.
    """
    url = f"http://127.0.0.1:{port}/json/list"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310  # loopback forward
            data = json.loads(resp.read())
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        raise WebViewTransportError(f"could not read WebView targets at {url}: {e}") from e
    if not isinstance(data, list):
        raise WebViewTransportError(f"WebView targets at {url} are not a list")
    return [t for t in data if isinstance(t, dict)]


def _socket_url(port: int, target: dict[str, Any]) -> str:
    """The target's WebSocket URL, re-pointed at the forwarded host port.

    Only the path is kept: the host the device writes into the URL is not one this host can reach.
    """
    raw = target.get("webSocketDebuggerUrl")
    if not isinstance(raw, str) or not raw:
        raise WebViewTransportError("the WebView page offers no debugger URL (already attached?)")
    return f"ws://127.0.0.1:{port}{urllib.parse.urlsplit(raw).path}"


class CdpDomSource:
    """`DomSource` over CDP for one Android device and app package."""

    def __init__(
        self,
        serial: str,
        package: str,
        run: adb.RunFn = adb.real_run,
        *,
        fetch: FetchTargets = fetch_targets,
        connect: Connect = connect_websocket,
        timeout: float = 10.0,
    ) -> None:
        self._serial = serial
        self._package = package
        self._run = run
        self._fetch = fetch
        self._connect = connect
        self._timeout = timeout
        self._conn: CdpConnection | None = None

    @contextmanager
    def session(self, webview_id: str, frame: Frame) -> Iterator[None]:  # noqa: ARG002  # DomSource shape
        """Hold one WebSocket to the page under `frame` for the length of a `web:` block.

        `webview_id` goes unused: a CDP target carries no native id, so the frame alone picks the page.
        Every forward this opens is removed on exit, whether the block passed or failed.

        Raises:
            WebViewTransportError: no socket, an adb failure, or the WebSocket cannot open.
            SelectorError: no page, or more than one, contains `frame`.
        """
        forwards: list[int] = []
        try:
            candidates: list[_Candidate] = []
            for name in cdp_discovery.find_sockets(self._run, self._serial, self._package):
                port = cdp_discovery.forward_socket(self._run, self._serial, name)
                forwards.append(port)
                candidates.extend((port, t) for t in self._fetch(port, self._timeout))
            port, target = pick_target(candidates, frame)
            conn = CdpConnection(
                self._connect(_socket_url(port, target), self._timeout), self._timeout
            )
            self._conn = conn
            try:
                yield
            finally:
                self._conn = None
                conn.close()
        finally:
            for port in forwards:
                cdp_discovery.remove_forward(self._run, self._serial, port)

    def _session(self) -> CdpConnection:
        if self._conn is None:
            raise WebViewTransportError("the WebView source was used outside a web: block session")
        return self._conn

    def query_dom(self, webview_id: str) -> list[Element]:  # noqa: ARG002  # DomSource shape
        """The page's elements, walked by the same `QUERY_JS` the web backend and iOS run."""
        records = self._session().evaluate(f"({QUERY_JS})()")
        if not isinstance(records, list):
            raise WebViewTransportError("the WebView DOM query returned no element list")
        return parse_dom(records)

    def tap_element(self, webview_id: str, point: Point) -> None:  # noqa: ARG002  # DomSource shape
        """A trusted touch at `point`, in the page's CSS-pixel space.

        Raises:
            ElementNotTappable: nothing but the page itself sits at the point, so the touch would
                actuate nothing.
        """
        conn = self._session()
        x, y = point
        # `Input.dispatchTouchEvent` reports success whatever sits at the point, so hit-test first —
        # the same refusal the iOS bridge's script click gives when `elementFromPoint` resolves nothing.
        # `<html>` and `<body>` sit under every in-viewport point, so landing on either means the
        # resolved element is not there either; `QUERY_JS` never yields them as a target.
        hit = (
            f"(() => {{ const h = document.elementFromPoint({x}, {y});"
            " return h !== null && h !== document.body && h !== document.documentElement; })()"
        )
        if not conn.evaluate(hit):
            raise ElementNotTappable(f"web: no element at ({x}, {y}) in the WebView viewport")
        conn.call(
            "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]}
        )
        conn.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    def type_text(self, webview_id: str, text: str) -> None:  # noqa: ARG002  # DomSource shape
        """Append `text` to the focused field, firing `input` and then `change`, as iOS does.

        Raises:
            WebViewTransportError: no field holds focus.
        """
        conn = self._session()
        if not conn.evaluate(_CARET_TO_END_JS):
            raise WebViewTransportError("web: no focused field in the WebView to type into")
        conn.call("Input.insertText", {"text": text})
        conn.evaluate(_FIRE_CHANGE_JS)

    def scroll_to(self, webview_id: str, element_id: str) -> None:  # noqa: ARG002  # DomSource shape
        """Scroll the element with `data-testid` `element_id` into view; a missing one is left as is."""
        self._session().evaluate(_scroll_js(element_id))

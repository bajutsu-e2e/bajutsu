"""A minimal Chrome DevTools Protocol (CDP) client over one WebSocket (BE-0482).

Request and response only: every call carries an id, the client reads frames until the reply with
that id arrives, and drops the event frames CDP interleaves. The WebSocket itself comes from the
optional `websockets` package (the `android` extra), imported on the first connect and never on the
default path, so a base install that never drives an Android WebView never needs it.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any, Protocol

# The install hint a missing package fails a block with — the same extra sync `scripts/install.sh`
# runs, through `provision`, when the configured backends include Android. A `uv pip install` would
# not stick: the next `uv sync` drops whatever `uv.lock` does not name.
_INSTALL_HINT = "uv sync --extra android"


class WebViewTransportError(Exception):
    """The channel to a WebView could not carry a request through.

    One type for every way either channel fails, so the run loop fails that one `web:` block with
    this reason instead of letting the exception discard the whole run. On Android (CDP): the socket
    is missing, the `websockets` package is absent, CDP answers with an error, the page has no field
    to type into, or an app restart closes the WebSocket mid-block. On iOS (the BajutsuKit bridge,
    `WebViewBridge`): the bridge is unreachable, answers with an HTTP error status, or answers with a
    body that is not a JSON object.
    """


class Transport(Protocol):
    """One text-frame WebSocket, as the CDP client needs it; a recorded exchange stands in for tests."""

    def send(self, message: str) -> None:
        """Send one text frame, or raise `WebViewTransportError` when the socket refuses it."""

    def recv(self, timeout: float) -> str:
        """The next text frame, or `WebViewTransportError` once `timeout` passes or the socket closes."""

    def close(self) -> None:
        """Close the socket; closing one that is already gone is not an error."""


class _WebSocketTransport:
    """`Transport` over a `websockets` synchronous client connection."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    def send(self, message: str) -> None:
        try:
            self._ws.send(message)
        except Exception as e:  # every websockets failure is one transport failure
            raise WebViewTransportError(
                f"WebView DevTools socket send failed: {type(e).__name__}: {e}"
            ) from e

    def recv(self, timeout: float) -> str:
        try:
            frame = self._ws.recv(timeout=timeout)
        except TimeoutError as e:
            raise WebViewTransportError(
                f"WebView DevTools socket gave no reply within {timeout:.1f}s"
            ) from e
        except Exception as e:  # a closed socket (navigation, app restart) included
            raise WebViewTransportError(
                f"WebView DevTools socket closed: {type(e).__name__}: {e}"
            ) from e
        return frame if isinstance(frame, str) else bytes(frame).decode("utf-8", "replace")

    def close(self) -> None:
        # Closing a dead socket has nothing left to report.
        with contextlib.suppress(Exception):
            self._ws.close()


def connect_websocket(url: str, timeout: float) -> Transport:
    """Open the WebSocket at `url`.

    Raises:
        WebViewTransportError: the `websockets` package is not installed, or the socket cannot open.
    """
    try:
        from websockets.sync.client import connect
    except ImportError as e:
        raise WebViewTransportError(
            f"the Android WebView source needs the `websockets` package; install it with "
            f"`{_INSTALL_HINT}`"
        ) from e
    try:
        # No `max_size`: one DOM query returns every element record of the page in a single frame.
        ws = connect(url, open_timeout=timeout, max_size=None)
    except Exception as e:  # refused, reset, or a failed handshake alike
        raise WebViewTransportError(
            f"could not open WebView DevTools socket {url}: {type(e).__name__}: {e}"
        ) from e
    return _WebSocketTransport(ws)


class CdpConnection:
    """Numbered CDP calls over one `Transport`, each waiting for its own reply."""

    def __init__(self, transport: Transport, timeout: float = 10.0) -> None:
        self._transport = transport
        self._timeout = timeout
        self._next_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one CDP command and return its `result`.

        Raises:
            WebViewTransportError: CDP answered with an error, or no reply came within the timeout.
        """
        self._next_id += 1
        call_id = self._next_id
        self._transport.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebViewTransportError(f"CDP {method} gave no reply within {self._timeout}s")
            try:
                reply = json.loads(self._transport.recv(remaining))
            except ValueError as e:
                raise WebViewTransportError(f"CDP {method} returned a malformed frame") from e
            # An event (no id), or a late reply to an earlier call that timed out: not this call's.
            if not isinstance(reply, dict) or reply.get("id") != call_id:
                continue
            if "error" in reply:
                raise WebViewTransportError(f"CDP {method} failed: {reply['error']}")
            result = reply.get("result")
            # A missing result would read downstream as a falsy value — "no element", "no focused
            # field" — and hide that the protocol itself misbehaved.
            if not isinstance(result, dict):
                raise WebViewTransportError(f"CDP {method} reply has no result object")
            return result

    def evaluate(self, expression: str) -> Any:
        """Evaluate `expression` in the page and return its value, serialized by value.

        Raises:
            WebViewTransportError: the call failed, or the script threw in the page.
        """
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            details = details if isinstance(details, dict) else {}
            exc = details.get("exception")
            text = (exc.get("description") if isinstance(exc, dict) else None) or details.get(
                "text"
            )
            raise WebViewTransportError(f"WebView script threw: {text}")
        return result.get("result", {}).get("value")

    def close(self) -> None:
        self._transport.close()

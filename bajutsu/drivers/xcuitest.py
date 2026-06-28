"""XCUITest backend — semantic actuation over a loopback HTTP channel (BE-0019).

Unlike idb (a subprocess CLI that taps frame-centre coordinates), XCUITest actuates from a resident
XCTest runner living on the Simulator, so Python and that runner talk over a small `127.0.0.1`
channel — the same loopback pattern `network.py` already uses, in the Python→runner direction. This
module is the **Python side** of that channel: it builds the requests, parses the responses, and maps
failures onto the shared `Driver` exceptions. The runner itself (a generic XCTest target in
`BajutsuKit`) is a separate, on-device slice; here the transport is injectable so the request/response
logic is exercised against a fake — no Simulator on the gate.

The crux is **element addressing**: resolution stays Python-side (`resolve_unique`), so the driver
acts on exactly the element it resolved by sending that element's opaque *per-snapshot handle* the
runner minted — never a re-resolved predicate that could match a different element. A handle that has
gone stale comes back as `stale` and surfaces as the same vanished-element error idb would raise.

Selection-wiring (adding `xcuitest` to `backends.IMPLEMENTED` / `make_driver`, plus the device
availability probe) lands with the runner; today the driver is constructed directly (e.g. in tests)
and `backends.capabilities_for` reads its `CAPABILITIES` without a device.
"""

from __future__ import annotations

import http.client
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bajutsu.drivers import base


class XcuitestChannelError(RuntimeError):
    """The runner channel failed: it never came up, stopped answering, or returned a bad response.

    An infrastructure failure, kept distinct from a test outcome — a crashed/absent runner fails the
    run loudly rather than being read as "element not found".
    """


@dataclass(frozen=True)
class _Reply:
    """A decoded runner response.

    `elements` carries the `GET /elements` payload (each item is the normalized element fields plus
    its `handle`); `png` carries raw `GET /screenshot` bytes.
    """

    status: str
    elements: list[dict[str, Any]] | None = None
    png: bytes | None = field(default=None, repr=False)


# (method, path, json body) -> decoded reply. Injectable so the channel logic is tested without a
# runner; the default talks HTTP to the runner's loopback server.
TransportFn = Callable[[str, str, Mapping[str, Any] | None], _Reply]

# Statuses the runner returns for an actuation request.
_OK = "ok"
_STALE = "stale"  # the resolved handle no longer maps to a live element (the screen changed)


def _to_element(item: Mapping[str, Any]) -> base.Element:
    """Normalize one `GET /elements` item into an `Element`.

    The `handle` is dropped: it is a channel address, not a selector field, so matching is unaffected.
    """
    frame = item.get("frame") or (0.0, 0.0, 0.0, 0.0)
    return {
        "identifier": item.get("identifier"),
        "label": item.get("label"),
        "value": item.get("value"),
        "traits": list(item.get("traits") or []),
        "frame": (float(frame[0]), float(frame[1]), float(frame[2]), float(frame[3])),
    }


def _decode(path: str, status_code: int, body: bytes) -> _Reply:
    """Decode a raw runner response into a `_Reply`.

    `/screenshot` returns raw PNG bytes; every other endpoint returns a small JSON object with a
    `status` (and, for `/elements`, an `elements` array). A non-200 still carries the server's
    `status` when present, so `not-found` / `stale` reach the driver as outcomes rather than as a
    transport error. Pure (no socket) so the wire format is unit-tested directly.
    """
    if path == "/screenshot":
        return _Reply(status=_OK if status_code == 200 else "error", png=body)
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise XcuitestChannelError(f"runner returned non-JSON for {path}: {body!r}") from exc
    status = data.get("status") or (_OK if status_code == 200 else "error")
    elements = data.get("elements")
    return _Reply(status=str(status), elements=elements)


def _http_transport(host: str, port: int) -> TransportFn:
    """The real transport: one short HTTP request to the runner's loopback server per call."""

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:  # pragma: no cover - exercised on-device against the real runner, not on the gate
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            return _decode(path, resp.status, resp.read())
        except OSError as exc:  # pragma: no cover - see above
            raise XcuitestChannelError(f"runner channel {method} {path} failed: {exc}") from exc
        finally:
            conn.close()

    return transport


class XcuitestDriver:
    """Driver for the iOS Simulator via a resident XCUITest runner (semantic, identifier-based)."""

    name = "xcuitest"

    # Beyond idb: a semantic tap (by handle, no coordinates), native condition waiting, and the
    # two-finger gestures idb raises UnsupportedAction for. No NETWORK — network evidence comes from
    # the app-side collector (BE-0020 boundary), not the actuator. A class constant so the preflight
    # (BE-0082) reads it via backends.capabilities_for without constructing a driver.
    CAPABILITIES = frozenset(
        {
            base.Capability.QUERY,
            base.Capability.ELEMENTS,
            base.Capability.SCREENSHOT,
            base.Capability.SEMANTIC_TAP,
            base.Capability.CONDITION_WAIT,
            base.Capability.MULTI_TOUCH,
        }
    )

    def __init__(
        self,
        *,
        transport: TransportFn | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._transport = transport if transport is not None else _http_transport(host, port)

    # --- the channel ---

    def _query_with_handles(self) -> tuple[list[base.Element], dict[int, str]]:
        """A snapshot plus a map from each element's object identity to its per-snapshot handle.

        Keyed by `id()` of the returned dicts: `resolve_unique` returns one of these very objects, so
        the resolved element's handle is an O(1) identity lookup — the element is acted on by the
        exact handle the runner minted for it, never re-resolved on the runner side.
        """
        reply = self._transport("GET", "/elements", None)
        elements: list[base.Element] = []
        handles: dict[int, str] = {}
        for item in reply.elements or []:
            el = _to_element(item)
            elements.append(el)
            handles[id(el)] = str(item.get("handle", ""))
        return elements, handles

    def _resolve_handle(self, sel: base.Selector) -> str:
        """Resolve *sel* to a unique element Python-side and return its snapshot handle.

        Raises `AmbiguousSelector` / `ElementNotFound` before any actuation request is sent.
        """
        elements, handles = self._query_with_handles()
        el = base.resolve_unique(elements, sel)
        return handles[id(el)]

    def _actuate(self, path: str, body: Mapping[str, Any], sel: base.Selector) -> None:
        reply = self._transport("POST", path, body)
        if reply.status == _OK:
            return
        if reply.status == _STALE:
            raise base.ElementNotFound(f"要素が消失（handle が stale）: {sel!r}")
        raise base.ElementNotFound(f"操作対象が見つからない（{reply.status}）: {sel!r}")

    # --- Driver Protocol ---

    def query(self) -> list[base.Element]:
        elements, _ = self._query_with_handles()
        return elements

    def tap(self, sel: base.Selector) -> None:
        self._actuate("/tap", {"handle": self._resolve_handle(sel)}, sel)

    def double_tap(self, sel: base.Selector) -> None:
        self._actuate("/tap", {"handle": self._resolve_handle(sel), "taps": 2}, sel)

    def long_press(self, sel: base.Selector, duration: float) -> None:
        self._actuate("/tap", {"handle": self._resolve_handle(sel), "duration": duration}, sel)

    def tap_point(self, p: base.Point) -> None:
        # A raw coordinate tap (system alerts and the like), the one path with no element/handle.
        reply = self._transport("POST", "/tap", {"point": [p[0], p[1]]})
        if reply.status != _OK:
            raise XcuitestChannelError(f"coordinate tap failed ({reply.status}) at {p}")

    def pinch(self, sel: base.Selector, scale: float) -> None:
        handle = self._resolve_handle(sel)
        self._actuate("/gesture", {"handle": handle, "kind": "pinch", "scale": scale}, sel)

    def rotate(self, sel: base.Selector, radians: float) -> None:
        handle = self._resolve_handle(sel)
        self._actuate("/gesture", {"handle": handle, "kind": "rotate", "radians": radians}, sel)

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        reply = self._transport("POST", "/swipe", {"from": [frm[0], frm[1]], "to": [to[0], to[1]]})
        if reply.status != _OK:
            raise XcuitestChannelError(f"swipe failed ({reply.status})")

    def type_text(self, text: str) -> None:
        reply = self._transport("POST", "/type", {"text": text})
        if reply.status != _OK:
            raise XcuitestChannelError(f"type failed ({reply.status})")

    def wait_for(self, sel: base.Selector, timeout: float, poll: float = 0.2) -> bool:
        """Poll `query()` until at least one element matches, or `timeout` elapses.

        A condition wait with no fixed sleep — mirroring the orchestrator's discipline and idb's
        `wait_for`.
        """
        deadline = time.monotonic() + timeout
        while True:
            if len(base.find_all(self.query(), sel)) >= 1:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll)

    def screenshot(self, path: str) -> None:
        reply = self._transport("GET", "/screenshot", None)
        Path(path).write_bytes(reply.png or b"")

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

    # --- lifecycle ---

    def await_ready(self, timeout: float = 10.0, poll: float = 0.1) -> None:
        """Block until the runner's loopback server answers `GET /health` with `ready`.

        A bounded, sleep-free condition wait: it fails loudly (`XcuitestChannelError`) on timeout
        rather than hanging, so "the runner never came up" is a clear run failure.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self._transport("GET", "/health", None).status == "ready":
                    return
            except XcuitestChannelError:
                pass  # not accepting connections yet; keep probing until the deadline
            if time.monotonic() >= deadline:
                raise XcuitestChannelError(
                    f"xcuitest runner did not come up within {timeout}s (health never ready)"
                )
            time.sleep(poll)

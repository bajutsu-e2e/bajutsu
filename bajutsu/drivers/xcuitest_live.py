"""The live-route XCUITest transport — W3C WebDriver against a reserved iOS device (BE-0238).

The local XCUITest path (`drivers/xcuitest.py`) drives a resident BajutsuKit runner over a bespoke
loopback HTTP channel. A device cloud exposes no such runner — only a W3C WebDriver endpoint
(Appium's XCUITest driver) for a device it has reserved. So the *live* route speaks W3C WebDriver to
that endpoint directly from Python, rather than tunnelling the runner channel to a port the grid does
not serve.

Two pieces live here, both faked at the network boundary so no grid is needed on the gate:

- `WebDriverClient` — a minimal in-house W3C client built on `http.client` and injected the same way
  `XcuitestDriver` injects its transport, so the wire mapping is exercised against a fake. It keeps
  the gate free of a third-party WebDriver dependency and matches the runner channel's own stdlib
  client.
- `XcuitestLiveDriver` — the driver, which reuses the shape of `XcuitestDriver`: query the whole
  screen with one broad locator, build the `base.Element` list, resolve the selector Python-side with
  `resolve_unique` (so an ambiguous selector fails immediately — determinism first, prime directive 2)
  and act on the chosen element by the WebDriver element id the query returned. The element id stands
  in for the runner's opaque handle in the same query-resolve-act-by-handle flow.

Slice A landed session lifecycle, `query` / `tap` / `screenshot` / readiness. Slice B wires input and
gestures onto Appium's XCUITest `mobile:` commands (over `POST /execute/sync`), the driver's native
counterparts of the local runner's semantic endpoints: a coordinate tap, double tap, touch-and-hold,
drag, pinch, and rotate, plus text entry through W3C send-keys to the active element. `selectAll` and
`copy` have no first-class Appium XCUITest command — the local runner does them natively — so they
fail loudly on the live route rather than silently no-op'ing (determinism first). The run-time
capability narrowing, config, and docs (Slice C) are the remaining follow-on.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import time
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from bajutsu.drivers import base
from bajutsu.evidence import intervals

# The W3C element-reference key: `findElements` returns each element as `{ELEMENT_KEY: "<id>"}`, and
# every per-element request addresses it by that opaque id — the live counterpart of the runner's
# opaque handle.
ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"

# The W3C key code for backspace: `delete_text` types it once per character to erase, the same run of
# backspaces the local runner's `/deleteText` issues on the focused field (BE-0265).
BACKSPACE_KEY = "\ue003"

# A drag needs a wall-clock duration (a real XCUITest swipe is a timed gesture, not an instant jump).
# The pinch velocity carries the sign Appium's `mobile: pinch` requires — positive to zoom in, negative
# to zoom out — so its magnitude is fixed and only the sign varies. `mobile: rotateElement` carries
# direction in `rotation` instead, so its velocity is a fixed positive rate (see the `rotate()` comment).
_DRAG_DURATION_SECONDS = 0.5
_PINCH_VELOCITY = 1.0
_ROTATE_VELOCITY = 1.0

# Per-request socket timeouts, split by idempotency the way the runner channel splits them: a read is
# tight, a write (a synthesized UI event) gets more headroom on a contended grid. Unlike the runner
# channel the live client does not retry — a WebDriver click cannot be re-issued safely after delivery
# — so each request simply fails loudly on timeout rather than hanging.
_READ_TIMEOUT_SECONDS = 15
_WRITE_TIMEOUT_SECONDS = 30

# (method, path, json body) -> (HTTP status, decoded JSON). Injectable so the wire mapping is tested
# against a fake; the default talks HTTP(S) to the grid's WebDriver endpoint.
WdTransportFn = Callable[[str, str, Mapping[str, Any] | None], tuple[int, Any]]


class WebDriverError(RuntimeError):
    """The WebDriver endpoint failed: it never answered, returned a non-WebDriver reply, or errored.

    An infrastructure failure, kept distinct from a test outcome — a wedged / absent grid fails the
    run loudly rather than being read as "element not found".
    """


def _timeout_for(method: str) -> float:
    return _READ_TIMEOUT_SECONDS if method == "GET" else _WRITE_TIMEOUT_SECONDS


def _raw_wd_transport(endpoint: str) -> WdTransportFn:
    """One HTTP(S) attempt to a WebDriver endpoint, decoding the JSON reply.

    The endpoint may carry a base path (e.g. `.../wd/hub`); it is prefixed to every request path so a
    relative `/session` resolves against it.
    """
    parsed = urllib.parse.urlparse(endpoint)
    base_path = parsed.path.rstrip("/")
    host = parsed.hostname or ""
    https = parsed.scheme == "https"
    port = parsed.port or (443 if https else 80)

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> tuple[int, Any]:
        conn_cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
        conn = conn_cls(host, port, timeout=_timeout_for(method))
        try:  # pragma: no cover - exercised against a real grid, not on the gate
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, base_path + path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            data = json.loads(raw) if raw else {}
            return resp.status, data
        except (
            OSError,
            http.client.HTTPException,
            json.JSONDecodeError,
        ) as exc:  # pragma: no cover - see above
            raise WebDriverError(f"WebDriver {method} {path} failed: {exc}") from exc
        finally:
            conn.close()

    return transport


class WebDriverClient:
    """A minimal in-house W3C WebDriver client over an injectable transport (BE-0238)."""

    def __init__(self, transport: WdTransportFn) -> None:
        self._transport = transport
        self._session: str | None = None

    def _value(self, method: str, path: str, body: Mapping[str, Any] | None) -> Any:
        """Send one request and return the WebDriver `value`, raising loudly on any error reply.

        Every W3C reply wraps its result in a `value`; a non-2xx status or a missing envelope is an
        endpoint failure, surfaced as `WebDriverError` rather than mistaken for a test outcome.
        """
        status, data = self._transport(method, path, body)
        if not isinstance(data, Mapping) or "value" not in data:
            raise WebDriverError(f"{method} {path}: malformed reply (status={status}): {data!r}")
        if status >= 400:
            raise WebDriverError(f"{method} {path} failed (status={status}): {data['value']!r}")
        return data["value"]

    def _session_path(self, suffix: str) -> str:
        if self._session is None:
            raise WebDriverError("no open WebDriver session")
        return f"/session/{self._session}{suffix}"

    def new_session(self, capabilities: Mapping[str, Any]) -> str:
        """Open a session with *capabilities* (W3C `alwaysMatch`) and return its id."""
        value = self._value(
            "POST", "/session", {"capabilities": {"alwaysMatch": dict(capabilities)}}
        )
        session = value.get("sessionId") if isinstance(value, Mapping) else None
        if not session:
            raise WebDriverError(f"new session returned no sessionId: {value!r}")
        self._session = str(session)
        return self._session

    def delete_session(self) -> None:
        """Close the open session (a no-op when none is open, so teardown is idempotent)."""
        if self._session is None:
            return
        self._transport("DELETE", f"/session/{self._session}", None)
        self._session = None

    def find_elements(self, using: str, value: str) -> list[str]:
        """Return the element ids matching a locator (empty when none match)."""
        found = self._value(
            "POST", self._session_path("/elements"), {"using": using, "value": value}
        )
        if not isinstance(found, list):
            raise WebDriverError(f"elements was not a list: {found!r}")
        ids: list[str] = []
        for item in found:
            if not isinstance(item, Mapping) or ELEMENT_KEY not in item:
                raise WebDriverError(f"element reply missing {ELEMENT_KEY!r}: {item!r}")
            ids.append(item[ELEMENT_KEY])
        return ids

    def attribute(self, element_id: str, name: str) -> Any:
        """Return one element attribute (`name` / `label` / `value` / `type` / `enabled` / …)."""
        return self._value(
            "GET", self._session_path(f"/element/{element_id}/attribute/{name}"), None
        )

    def rect(self, element_id: str) -> Mapping[str, Any]:
        """Return an element's bounding rect (`x` / `y` / `width` / `height`)."""
        value = self._value("GET", self._session_path(f"/element/{element_id}/rect"), None)
        if not isinstance(value, Mapping):
            raise WebDriverError(f"rect was not a mapping: {value!r}")
        return value

    def click(self, element_id: str) -> None:
        """Tap the element addressed by *element_id*."""
        self._value("POST", self._session_path(f"/element/{element_id}/click"), {})

    def screenshot(self) -> bytes:
        """Return the current screen as PNG bytes (the endpoint returns them base64-encoded)."""
        encoded = self._value("GET", self._session_path("/screenshot"), None)
        try:
            return base64.b64decode(encoded)
        except (binascii.Error, TypeError) as exc:
            raise WebDriverError(f"screenshot was not valid base64: {encoded!r}") from exc

    def is_ready(self) -> bool:
        """Whether the endpoint reports itself ready to serve (`GET /status`)."""
        value = self._value("GET", "/status", None)
        return bool(value.get("ready")) if isinstance(value, Mapping) else False

    def execute(self, script: str, args: list[Any]) -> Any:
        """Run an Appium `mobile:` command (`POST /execute/sync`) and return its result.

        The live route's gestures are Appium's XCUITest `mobile:` commands — the native counterparts of
        the local runner's semantic endpoints — driven through the standard W3C execute-script channel.
        """
        return self._value(
            "POST", self._session_path("/execute/sync"), {"script": script, "args": args}
        )

    def active_element(self) -> str:
        """Return the focused element's id (`GET /element/active`), for text entry."""
        value = self._value("GET", self._session_path("/element/active"), None)
        if not isinstance(value, Mapping) or ELEMENT_KEY not in value:
            raise WebDriverError(f"active element reply missing {ELEMENT_KEY!r}: {value!r}")
        return str(value[ELEMENT_KEY])

    def send_keys(self, element_id: str, text: str) -> None:
        """Type *text* into the element addressed by *element_id* (`POST /element/{id}/value`)."""
        self._value("POST", self._session_path(f"/element/{element_id}/value"), {"text": text})


def _norm_type(type_: str) -> str:
    """Normalize an XCUITest element type (`XCUIElementTypeButton`) to a common trait (`button`)."""
    t = type_.removeprefix("XCUIElementType")
    return t[:1].lower() + t[1:] if t else t


def _str_or_none(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _is_true(value: Any) -> bool:
    """Whether a WebDriver attribute that Appium returns as a `"true"` / `"false"` string is true."""
    return str(value).lower() == "true"


class XcuitestLiveDriver:
    """Drive a reserved iOS device over W3C WebDriver, resolving selectors Python-side (BE-0238)."""

    name = "xcuitest"

    # What a live Appium / WebDriver grid reaches: a semantic tap, condition waits, screenshots, and —
    # since Slice B wires them onto `mobile: pinch` / `mobile: rotateElement` — the two-finger gestures
    # (MULTI_TOUCH). The simctl-backed device-control family and permission grants never apply to a real
    # cloud device, so they stay unadvertised; the run-time capability narrowing is Slice C.
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

    def __init__(self, client: WebDriverClient) -> None:
        self._client = client

    # --- query / resolve / act ---

    def _query_with_handles(self) -> tuple[list[base.Element], dict[int, str]]:
        """A snapshot plus a map from each element's object identity to its WebDriver element id.

        Keyed by `id()` of the returned dicts, exactly as the runner-channel driver keys its handles:
        `resolve_unique` returns one of these very objects, so the resolved element's WebDriver id is
        an O(1) identity lookup — the element is acted on by the id the query returned, never
        re-resolved server-side.
        """
        # One broad `findElements` for the handles, then the attributes per element. Correctness over
        # round-trips for this seam-establishing slice; a bulk page-source read is a follow-on perf
        # pass. The gate fakes the wire, so the chattiness costs nothing here.
        element_ids = self._client.find_elements("xpath", "//*")
        elements: list[base.Element] = []
        handles: dict[int, str] = {}
        for element_id in element_ids:
            el = self._snapshot(element_id)
            elements.append(el)
            handles[id(el)] = element_id
        return elements, handles

    def _snapshot(self, element_id: str) -> base.Element:
        traits: list[str] = []
        type_ = self._client.attribute(element_id, "type")
        if isinstance(type_, str) and type_:
            traits.append(_norm_type(type_))
        if not _is_true(self._client.attribute(element_id, "enabled")):
            traits.append(base.Trait.NOT_ENABLED)
        if _is_true(self._client.attribute(element_id, "selected")):
            traits.append(base.Trait.SELECTED)
        r = self._client.rect(element_id)
        try:
            frame = (
                float(r.get("x", 0)),
                float(r.get("y", 0)),
                float(r.get("width", 0)),
                float(r.get("height", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise WebDriverError(f"rect had non-numeric coordinate: {r!r}") from exc
        return {
            "identifier": _str_or_none(self._client.attribute(element_id, "name")),
            "label": _str_or_none(self._client.attribute(element_id, "label")),
            "value": _str_or_none(self._client.attribute(element_id, "value")),
            "traits": traits,
            "frame": frame,
        }

    def query(self) -> list[base.Element]:
        elements, _ = self._query_with_handles()
        return elements

    def _resolve_handle(self, sel: base.Selector) -> str:
        """Resolve *sel* to a single element Python-side and return its WebDriver id.

        The one resolution point every element-targeted gesture shares: an ambiguous selector fails
        here, before any actuation (determinism first), exactly as the runner-channel driver resolves.
        """
        elements, handles = self._query_with_handles()
        el = base.resolve_unique(elements, sel)
        return handles[id(el)]

    def tap(self, sel: base.Selector) -> None:
        self._client.click(self._resolve_handle(sel))

    def back(self) -> None:
        # No hardware back on iOS: tap the OS navigation back button, the same element the other iOS
        # backends tap (BE-0210), reusing `tap` so resolution stays Python-side.
        self.tap({"id": base.OS_BACK_BUTTON})

    def wait_for(self, sel: base.Selector) -> bool:
        """Single-shot: whether `sel` matches the current screen (BE-0118).

        The deadline poll lives in the shared `base.wait_until`, so a caller's timeout means the same
        real seconds on every backend.
        """
        return base.default_wait_for(self, sel)

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(self._client.screenshot())

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

    def driver_interval(self, kind: str, path: Path) -> intervals.Interval | None:
        # Returning None for every kind routes the evidence FileSink through the driver path rather
        # than the simctl path (which calls `simctl.validated_udid(endpoint)` and crashes on a URL).
        # In-driver recording over WebDriver actions is Slice B.
        return None

    # --- Slice B: input and gestures, mapped onto Appium's XCUITest `mobile:` commands ---

    def tap_point(self, p: base.Point) -> None:
        # A raw coordinate tap (system alerts and the like), the one path with no element/handle.
        self._client.execute("mobile: tap", [{"x": p[0], "y": p[1]}])

    def double_tap(self, sel: base.Selector) -> None:
        self._client.execute("mobile: doubleTap", [{"elementId": self._resolve_handle(sel)}])

    def long_press(self, sel: base.Selector, duration: float) -> None:
        self._client.execute(
            "mobile: touchAndHold", [{"elementId": self._resolve_handle(sel), "duration": duration}]
        )

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._client.execute(
            "mobile: dragFromToForDuration",
            [
                {
                    "fromX": frm[0],
                    "fromY": frm[1],
                    "toX": to[0],
                    "toY": to[1],
                    "duration": _DRAG_DURATION_SECONDS,
                }
            ],
        )

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # A real XCUITest drag scrolls iOS scroll views, so a directional scroll is just a swipe —
        # the same identity the runner-channel driver keeps.
        self.swipe(frm, to)

    def pinch(self, sel: base.Selector, scale: float) -> None:
        # Appium's `mobile: pinch` needs a velocity whose sign matches the scale: positive to zoom in
        # (scale > 1), negative to zoom out (scale < 1).
        velocity = _PINCH_VELOCITY if scale >= 1 else -_PINCH_VELOCITY
        self._client.execute(
            "mobile: pinch",
            [{"elementId": self._resolve_handle(sel), "scale": scale, "velocity": velocity}],
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        # `rotation` carries the signed direction; `velocity` is a rate/magnitude (always positive),
        # the same convention `XCUIElement.rotate(_:withVelocity:)` uses and codegen/xcuitest.py
        # emits with a fixed `withVelocity: 1.0`.
        self._client.execute(
            "mobile: rotateElement",
            [
                {
                    "elementId": self._resolve_handle(sel),
                    "rotation": radians,
                    "velocity": _ROTATE_VELOCITY,
                }
            ],
        )

    def type_text(self, text: str) -> None:
        # Type into the focused field, as the runner's `/type` does: W3C send-keys to the active
        # element (Appium's XCUITest driver focuses the field the scenario tapped first).
        self._client.send_keys(self._client.active_element(), text)

    def delete_text(self, count: int) -> None:
        # A run of backspaces on the focused field (BE-0265) — the W3C backspace key, once per count.
        self._client.send_keys(self._client.active_element(), BACKSPACE_KEY * count)

    def select_all(self) -> None:
        raise base.UnsupportedAction(
            "selectAll is not reachable over the live Appium / WebDriver route (BE-0238); it has no "
            "first-class XCUITest command"
        )

    def copy_selection(self) -> None:
        raise base.UnsupportedAction(
            "copy is not reachable over the live Appium / WebDriver route (BE-0238); it has no "
            "first-class XCUITest command"
        )

    def select_option(self, sel: base.Selector, option: str) -> None:
        raise base.UnsupportedAction("selectOption is web-only; iOS has no native <select>")

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
        # BE-0316 targets the resident-runner XCUITest backend's SpringBoard query channel, which a
        # live Appium / WebDriver grid does not expose here; this backend does not advertise the
        # capability, so preflight rejects the step and this is only the mid-run backstop.
        raise base.UnsupportedAction(
            "handleSystemAlert is served by the resident-runner XCUITest backend; not on the live grid"
        )

    # --- lifecycle ---

    def await_ready(self, timeout: float = 10.0, poll: float = 0.1) -> None:
        """Block until the WebDriver endpoint reports ready, or fail loudly on timeout.

        A bounded condition wait mirroring the runner channel's `/health` poll: it polls `GET /status`
        (no fixed sleep) and raises `WebDriverError` on timeout rather than hanging, so "the grid never
        came up" is a clear run failure.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self._client.is_ready():
                    return
            except WebDriverError:
                pass  # not answering yet; keep probing until the deadline
            if time.monotonic() >= deadline:
                raise WebDriverError(f"WebDriver endpoint did not become ready within {timeout}s")
            time.sleep(poll)

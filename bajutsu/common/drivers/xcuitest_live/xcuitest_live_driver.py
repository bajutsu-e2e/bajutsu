"""The live-route iOS driver: a reserved cloud device over W3C WebDriver (BE-0238)."""

from __future__ import annotations

import time
from pathlib import Path

from bajutsu.common.drivers import base
from bajutsu.common.drivers.actuation import Actuation, ActuationLog, Drained
from bajutsu.common.evidence import intervals

from ._functions import _is_true, _norm_type, _str_or_none
from .web_driver_client import WebDriverClient
from .web_driver_error import WebDriverError

# The W3C key code for backspace: `delete_text` types it once per character to erase, the same run of
# backspaces the local runner's `/deleteText` issues on the focused field (BE-0265).
BACKSPACE_KEY = "\ue003"

# A drag needs a wall-clock duration (a real XCUITest swipe is a timed gesture, not an instant jump).
# The pinch velocity carries the sign Appium's `mobile: pinch` requires — positive to zoom in, negative
# to zoom out — so its magnitude is fixed and only the sign varies. `mobile: rotateElement` carries
# direction in `rotation` instead, so its velocity is a fixed positive rate (see the `rotate()` comment).
_DRAG_DURATION_SECONDS = 0.5
# `scroll` drags over a longer duration than a plain `swipe` so the scroll view settles where the
# gesture ends rather than flinging past it — the non-inertial contract (BE-0326).
_SCROLL_DURATION_SECONDS = 1.0
_PINCH_VELOCITY = 1.0
_ROTATE_VELOCITY = 1.0

# iOS reports every frame and coordinate in points, on this route as on the runner channel.
_UNIT = "point"


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
        # The device screen size (BE-0326), fetched once from the session; fixed for a session.
        self._screen: base.Point | None = None
        # What this driver actually actuated, drained per step by the run loop.
        self._actuations = ActuationLog()

    def drain_actuations(self) -> Drained:
        """The concrete actuations performed since the last drain (`ActuationReporter`)."""
        return self._actuations.drain()

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
            "nativeZ": None,  # WebDriverAgent exposes no z signal (BE-0355)
        }

    def query(self) -> list[base.Element]:
        elements, _ = self._query_with_handles()
        return elements

    def _resolve_handle(self, sel: base.Selector) -> tuple[str, base.Element]:
        """Resolve *sel* to a single element Python-side; return its WebDriver id and the element.

        The one resolution point every element-targeted gesture shares: an ambiguous selector fails
        here, before any actuation (determinism first), exactly as the runner-channel driver resolves.
        The element travels with the id so the caller can record what it actuated without a second
        query.
        """
        elements, handles = self._query_with_handles()
        el = base.resolve_unique(elements, sel)
        return handles[id(el)], el

    def _log_element(
        self,
        gesture: str,
        el: base.Element,
        *,
        duration_s: float | None = None,
        scale: float | None = None,
        radians: float | None = None,
    ) -> None:
        """Record an element-targeted gesture; the WebDriver server picks the touch point, so no point."""
        self._actuations.record(
            Actuation(
                gesture=gesture,
                via="handle",
                unit=_UNIT,
                frame=el["frame"],
                target=el["identifier"],
                duration_s=duration_s,
                scale=scale,
                radians=radians,
            )
        )

    def _resolve_handle_checked(self, sel: base.Selector) -> tuple[str, base.Element]:
        """`_resolve_handle`, but raises `ElementNotTappable` when the target is covered.

        Shares `is_tappable`'s own document-order proxy (`topmost_at_point`) directly over this
        one query, rather than resolving twice by also calling `is_tappable`.
        """
        elements, handles = self._query_with_handles()
        el = base.resolve_unique(elements, sel)
        base.raise_if_covered(elements, el, sel)
        return handles[id(el)], el

    def tap(self, sel: base.Selector) -> None:
        handle, el = self._resolve_handle_checked(sel)
        self._log_element("tap", el)
        self._client.click(handle)

    def is_tappable(self, sel: base.Selector) -> bool:
        # The local runner route (`drivers/xcuitest.py`) reads XCTest's own `isHittable` directly;
        # this live route only has a W3C WebDriver page-source query, with no such property
        # surfaced through Appium's XCUITest driver here. Falls back to the same document-order
        # proxy adb uses (`topmost_at_point`), with the same caveat: a heuristic, not the native
        # signal the local route gets.
        try:
            elements, _ = self._query_with_handles()
            target = base.resolve_unique(elements, sel)
        except base.ElementNotFound:
            return False
        return base.topmost_at_point(elements, base.frame_center(target["frame"]), target) is None

    def back(self) -> None:
        # No hardware back on iOS: tap the OS navigation back button, the same element the other iOS
        # backends tap (BE-0210). Resolves via `_resolve_handle`, not `tap`'s `_resolve_handle_checked`
        # occlusion check: on this route that check is only the `topmost_at_point` document-order
        # proxy (this class has no native `isHittable`), and a full-screen sibling emitted after the
        # nav bar in the page source can misjudge the back button as covered — with no recovery
        # wrapper here (unlike an author's `tap` step, `_do_back` calls `driver.back()` directly), a
        # false positive would fail the step outright rather than costing a few scroll steps.
        handle, el = self._resolve_handle({"id": base.OS_BACK_BUTTON})
        self._log_element("tap", el)
        self._client.click(handle)

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

    def driver_interval(self, kind: str, path: Path) -> intervals.Interval | None:  # noqa: ARG002  # Driver shape
        # Returning None for every kind routes the evidence FileSink through the driver path rather
        # than the simctl path (which calls `simctl.validated_udid(endpoint)` and crashes on a URL).
        # In-driver recording over WebDriver actions is Slice B (BE-0238).
        return None

    # --- Slice B: input and gestures, mapped onto Appium's XCUITest `mobile:` commands ---

    def tap_point(self, p: base.Point) -> None:
        # A raw coordinate tap (system alerts and the like), the one path with no element/handle.
        self._actuations.record(Actuation(gesture="tap", via="coordinate", unit=_UNIT, points=(p,)))
        self._client.execute("mobile: tap", [{"x": p[0], "y": p[1]}])

    def double_tap(self, sel: base.Selector) -> None:
        handle, el = self._resolve_handle_checked(sel)
        self._log_element("doubleTap", el)
        self._client.execute("mobile: doubleTap", [{"elementId": handle}])

    def long_press(self, sel: base.Selector, duration: float) -> None:
        handle, el = self._resolve_handle_checked(sel)
        self._log_element("longPress", el, duration_s=duration)
        self._client.execute("mobile: touchAndHold", [{"elementId": handle, "duration": duration}])

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._actuations.record(
            Actuation(gesture="swipe", via="coordinate", unit=_UNIT, points=(frm, to))
        )
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

    def viewport(self) -> base.Point:
        # The device screen size (BE-0326). The queried tree can hold buffered off-screen ScrollView
        # children (a lazy list), so `screen_size_from_elements` overshoots the screen and the `scroll`
        # stop condition would judge an off-screen center as on-screen; the WebDriver window rect is
        # the real screen (an iOS app window fills it). Cached for the session.
        if self._screen is None:
            r = self._client.window_rect()
            self._screen = (float(r["width"]), float(r["height"]))
        return self._screen

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        self._actuations.record(
            Actuation(gesture="scroll", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        # A non-inertial pan (BE-0326): `mobile: dragFromToForDuration` over a longer duration than a
        # plain drag keeps the scroll view moving with the finger and settling where it ends, leaving
        # no fling momentum. A quick flick's post-lift travel is device- and frame-rate-dependent —
        # the non-determinism the `scroll` action removes by re-querying after each bounded step.
        self._client.execute(
            "mobile: dragFromToForDuration",
            [
                {
                    "fromX": frm[0],
                    "fromY": frm[1],
                    "toX": to[0],
                    "toY": to[1],
                    "duration": _SCROLL_DURATION_SECONDS,
                }
            ],
        )

    def pinch(self, sel: base.Selector, scale: float) -> None:
        # Appium's `mobile: pinch` needs a velocity whose sign matches the scale: positive to zoom in
        # (scale > 1), negative to zoom out (scale < 1).
        velocity = _PINCH_VELOCITY if scale >= 1 else -_PINCH_VELOCITY
        handle, el = self._resolve_handle(sel)
        self._log_element("pinch", el, scale=scale)
        self._client.execute(
            "mobile: pinch",
            [{"elementId": handle, "scale": scale, "velocity": velocity}],
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        # `rotation` carries the signed direction; `velocity` is a rate/magnitude (always positive),
        # the same convention `XCUIElement.rotate(_:withVelocity:)` uses and codegen/xcuitest.py
        # emits with a fixed `withVelocity: 1.0`.
        handle, el = self._resolve_handle(sel)
        self._log_element("rotate", el, radians=radians)
        self._client.execute(
            "mobile: rotateElement",
            [
                {
                    "elementId": handle,
                    "rotation": radians,
                    "velocity": _ROTATE_VELOCITY,
                }
            ],
        )

    def type_text(self, text: str) -> None:
        # Type into the focused field, as the runner's `/type` does: W3C send-keys to the active
        # element (Appium's XCUITest driver focuses the field the scenario tapped first).
        # `text` is deliberately absent from the record — not even its length (see `actuation.py`).
        self._actuations.record(Actuation(gesture="typeText", via="focused", unit=_UNIT))
        self._client.send_keys(self._client.active_element(), text)

    def delete_text(self, count: int) -> None:
        # A run of backspaces on the focused field (BE-0265) — the W3C backspace key, once per count.
        self._actuations.record(Actuation(gesture="deleteText", via="focused", unit=_UNIT))
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

    def select_option(self, sel: base.Selector, option: str) -> None:  # noqa: ARG002  # Driver shape
        raise base.UnsupportedAction("selectOption is web-only; iOS has no native <select>")

    def set_picker_value(self, sel: base.Selector, value: str) -> None:  # noqa: ARG002  # Driver shape
        # The same platform as the resident runner, so a live-route implementation through Appium's
        # XCUITest driver may well be possible — but that is its own evaluation, and BE-0356 does not
        # commit to it. Refuse loudly meanwhile rather than silently doing nothing.
        raise base.UnsupportedAction(
            "setPickerValue is not implemented on the XCUITest live route (BE-0356)"
        )

    def enter_app(self, bundle_id: str) -> None:  # noqa: ARG002  # Driver shape
        # app: is scoped to the resident runner's own app stack for its first slice; a
        # live Appium / WebDriver grid session has no equivalent here. This backend does not
        # advertise APP_CONTEXT, so preflight rejects the step; this is only the mid-run backstop.
        raise base.UnsupportedAction(
            "app is served by the resident-runner XCUITest backend; not on the live grid"
        )

    def leave_app(self) -> None:
        raise base.UnsupportedAction(
            "app is served by the resident-runner XCUITest backend; not on the live grid"
        )

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:  # noqa: ARG002  # Driver shape
        # BE-0316 targets the resident-runner XCUITest backend's SpringBoard query channel, which a
        # live Appium / WebDriver grid does not expose here; this backend does not advertise the
        # capability, so preflight rejects the step and this is only the mid-run backstop.
        raise base.UnsupportedAction(
            "handleSystemAlert is served by the resident-runner XCUITest backend; not on the live grid"
        )

    def system_alert_labels(self) -> list[str]:
        # The SpringBoard query channel is a resident-runner capability the live grid does not expose;
        # this backend does not advertise HANDLE_SYSTEM_ALERT, so the reactive native path never runs.
        return []

    def notification_banner_frame(self) -> base.Frame | None:
        # This backend does not advertise HANDLE_NOTIFICATION_BANNER, for the same reason.
        return None

    def dismiss_blocking_tip(self, tree: list[base.Element] | None = None) -> bool:  # noqa: ARG002  # Driver shape
        # This backend does not advertise HANDLE_TIPKIT_TIP, so neither guard calls it.
        return False

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

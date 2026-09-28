"""The driver wrapper that resolves selectors against a WebView's DOM, not the native tree."""

from __future__ import annotations

from bajutsu.common.drivers.actuation import Actuation, ActuationLog, Drained
from bajutsu.common.drivers.base import (
    Capability,
    Element,
    ElementNotFound,
    Frame,
    Point,
    Selector,
    UnsupportedAction,
    find_all,
    frame_center,
    resolve_unique,
)

from .dom_source import DomSource

# A WebView reports its DOM rects in CSS pixels, in the WebView's own coordinate space rather than the
# device screen's — so a recorded point is read against the WebView, not against a device screenshot.
_UNIT = "cssPixel"


class WebContextDriver:
    """Driver wrapper that resolves selectors against a WebView's DOM instead of the native tree.

    Created by the run loop when entering a ``web`` block; delegates query/tap to the bridge and
    rejects actions the first slice does not support (swipe, type, pinch, rotate).
    """

    name = "webview"

    def __init__(self, bridge: DomSource, webview_id: str) -> None:
        self._bridge = bridge
        self._webview_id = webview_id
        # What this driver actually actuated, drained per step by the run loop. The bridge takes a
        # point, so this driver chooses its own coordinates — in the WebView's own space, not the
        # device screen's.
        self._actuations = ActuationLog()

    def drain_actuations(self) -> Drained:
        """The concrete actuations performed since the last drain (`ActuationReporter`)."""
        return self._actuations.drain()

    def query(self) -> list[Element]:
        return self._bridge.query_dom(self._webview_id)

    def _center(self, sel: Selector) -> Point:
        el = resolve_unique(self.query(), sel)
        x, y, w, h = el["frame"]
        return (x + w / 2, y + h / 2)

    def _log_point(self, gesture: str, point: Point, el: Element) -> None:
        """Record a gesture at a point this driver computed and handed the bridge."""
        self._actuations.record(
            Actuation(
                gesture=gesture,
                via="coordinate",
                unit=_UNIT,
                points=(point,),
                frame=el["frame"],
                target=el["identifier"],
            )
        )

    def is_tappable(self, sel: Selector) -> bool:
        # The bridge protocol (`DomSource`) exposes no point-based hit-test, unlike the native web
        # backend's `document.elementFromPoint` — a WebView-internal occlusion check is out of scope
        # for this first slice, matching its other not-yet-supported gestures. Resolved-but-uncheckable
        # counts as tappable, keeping today's behavior (no check) rather than silently disabling this
        # driver's tap; unresolved still counts as not tappable, like every other backend.
        try:
            resolve_unique(self.query(), sel)
        except ElementNotFound:
            return False
        return True

    def tap(self, sel: Selector) -> None:
        el = resolve_unique(self.query(), sel)
        eid = el.get("identifier")
        if eid:
            # Recorded as its own actuation: it moves the content the tap's point was computed
            # against, which is exactly the "the coordinate was computed against a screen that then
            # changed" class the record exists to make visible. The bridge scrolls by element, not by
            # coordinate, so there is no point to state.
            self._actuations.record(
                Actuation(gesture="scroll", via="bridge", unit=_UNIT, target=eid)
            )
            self._bridge.scroll_to(self._webview_id, eid)
        x, y, w, h = el["frame"]
        point = (x + w / 2, y + h / 2)
        self._log_point("tap", point, el)
        self._bridge.tap_element(self._webview_id, point)

    def tap_point(self, p: Point) -> None:
        self._actuations.record(Actuation(gesture="tap", via="coordinate", unit=_UNIT, points=(p,)))
        self._bridge.tap_element(self._webview_id, p)

    def double_tap(self, sel: Selector) -> None:
        el = resolve_unique(self.query(), sel)
        point = frame_center(el["frame"])
        self._log_point("doubleTap", point, el)
        self._bridge.tap_element(self._webview_id, point)

    def long_press(self, sel: Selector, duration: float) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("long_press is not supported in web context (first slice)")

    def swipe(self, frm: Point, to: Point) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("swipe is not supported in web context (first slice)")

    def scroll(self, frm: Point, to: Point) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("scroll is not supported in web context (first slice)")

    def back(self) -> None:
        raise UnsupportedAction("back is not supported in web context (first slice)")

    def pinch(self, sel: Selector, scale: float) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("pinch is not supported in web context (first slice)")

    def rotate(self, sel: Selector, radians: float) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("rotate is not supported in web context (first slice)")

    def type_text(self, text: str) -> None:
        # `text` is deliberately absent from the record — not even its length (see `actuation.py`).
        self._actuations.record(Actuation(gesture="typeText", via="focused", unit=_UNIT))
        self._bridge.type_text(self._webview_id, text)

    def delete_text(self, count: int) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("delete/clear is not supported in web context (first slice)")

    def select_all(self) -> None:
        raise UnsupportedAction("select is not supported in web context (first slice)")

    def copy_selection(self) -> None:
        raise UnsupportedAction("copy is not supported in web context (first slice)")

    def select_option(self, sel: Selector, option: str) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("selectOption is not supported in web context (first slice)")

    def set_picker_value(self, sel: Selector, value: str) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("setPickerValue is iOS-only; a DOM has no picker wheel")

    def select_photos(self, indices: list[int], *, timeout: float) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("selectPhotos is iOS-only; a DOM has no photo picker")

    def handle_system_alert(self, sel: Selector, timeout: float) -> None:  # noqa: ARG002  # Driver shape
        # BE-0316 taps an iOS SpringBoard prompt; a WebView DOM context has no OS-level alert, and
        # only the resident-runner XCUITest backend declares the capability, so this never runs.
        raise UnsupportedAction("handleSystemAlert is iOS-only; not supported in web context")

    def enter_app(self, bundle_id: str) -> None:  # noqa: ARG002  # Driver shape
        # app: rests on XCUITest's own cross-app activate(); a WebView's DOM context has
        # no bundle-id concept to switch to, and only the resident-runner XCUITest backend declares
        # APP_CONTEXT, so this never runs.
        raise UnsupportedAction("app is iOS-only; not supported in web context")

    def leave_app(self) -> None:
        raise UnsupportedAction("app is iOS-only; not supported in web context")

    def system_alert_labels(self) -> list[str]:
        # A WebView DOM context sees no SpringBoard alert layer; the reactive native path never runs.
        return []

    def notification_banner_frame(self) -> Frame | None:
        # A WebView DOM context sees no SpringBoard banner layer either; nothing to report here.
        return None

    def dismiss_blocking_tip(self, tree: list[Element] | None = None) -> bool:  # noqa: ARG002  # Driver shape
        # A TipKit tip is native UIKit, outside the DOM this context sees; nothing to dismiss here.
        return False

    def wait_for(self, sel: Selector) -> bool:
        """Single-shot: whether `sel` matches the WebView's current DOM (BE-0118).

        The deadline poll lives in the shared `base.wait_until`, so the timeout is honoured
        identically on every backend.
        """
        return bool(find_all(self.query(), sel))

    def screenshot(self, path: str) -> None:  # noqa: ARG002  # Driver shape
        raise UnsupportedAction("screenshot is not supported in web context (first slice)")

    def capabilities(self) -> set[str]:
        return {Capability.QUERY, Capability.WEBVIEW}

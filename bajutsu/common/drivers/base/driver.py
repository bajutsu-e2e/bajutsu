"""The interface every backend sits behind — the seam a platform is a backend through."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._shared import Frame, Point
from .element import Element
from .selector import Selector


@runtime_checkable
class Driver(Protocol):
    """Common interface for every backend.

    Actions (tap/type/swipe/wait/query) are performed by the actuator only. On a
    backend without semantic tap (a coordinate-only backend), the abstraction resolves the frame
    center via query() / resolve_unique() and taps by coordinates.
    """

    # Backend identifier (e.g. "xcuitest", "fake"). Recorded in the run
    # manifest and shown in the report so a run says which actuator drove it.
    name: str

    def query(self) -> list[Element]: ...
    def tap(self, sel: Selector) -> None: ...
    # Whether `sel` resolves to exactly one element that is actually reachable at its own point —
    # not covered by another on-screen element, or refused by the platform's own hit-test — realized
    # the idiomatic way per backend (iOS: native `isHittable`; web: a `document.elementFromPoint`
    # hit-test; adb: a document-order geometric check, `topmost_at_point` below). A pure query: it
    # never actuates and never scrolls, so `tap` can call it once to guard the actuation and the
    # scroll-recovery loop (`scroll_until_tappable`) can call it again, repeatedly, with no side
    # effects. `resolve_unique`'s own selector-ambiguity contract is unchanged by this — an ambiguous
    # `sel` still raises `AmbiguousSelector` immediately rather than being folded into `False`.
    def is_tappable(self, sel: Selector) -> bool: ...
    def tap_point(self, p: Point) -> None: ...  # raw coordinate tap (system alerts, etc.)
    def double_tap(self, sel: Selector) -> None: ...
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    # A directional scroll gesture — reveal off-screen content by travelling `frm`→`to` (BE-0227).
    # Distinct from `swipe`, which is a raw pointer drag for its own sake (a canvas / map pan / drag
    # handle). Most backends realize a scroll with a real drag, so they delegate `scroll` to `swipe`;
    # the web backend cannot (a mouse drag does not scroll a page), so it dispatches a wheel event
    # (desktop) or a touch drag (touch context) instead. The `swipe` handler routes the directional
    # form here and the coordinate form to `swipe`, so the coordinate form stays a literal drag.
    #
    # The step is non-inertial (BE-0326): content travels with the gesture and stops where it ends,
    # leaving no momentum carry. A fling's post-lift travel depends on scroll physics and frame
    # rate, which makes a target land above the fold on a fast device and below it on a slow one —
    # the non-determinism the `scroll` action removes by re-querying after each bounded step. Web is
    # already non-inertial; adb pans with a long-duration `input swipe`; XCUITest holds the drag at
    # its end before lifting. The `scroll` action's bounded re-query loop relies on this contract,
    # pinned by the driver conformance suite (BE-0114).
    def scroll(self, frm: Point, to: Point) -> None: ...
    # Navigate back one level, each backend using its platform-correct primitive (BE-0210):
    # Android's system back key, iOS's on-screen OS back button, the browser's history.
    def back(self) -> None: ...
    # Two-finger gestures. scale > 1 zooms in, < 1 zooms out; radians > 0 rotates
    # clockwise. Only backends advertising MULTI_TOUCH support these.
    def pinch(self, sel: Selector, scale: float) -> None: ...
    def rotate(self, sel: Selector, radians: float) -> None: ...
    def type_text(self, text: str) -> None: ...
    # Text-editing primitives on the currently focused field (the orchestrator focuses it with a
    # `tap` first, the same contract `type_text` relies on) — BE-0265. `delete_text` removes `count`
    # characters from the end (backspace-equivalent); `select_all` selects the whole content;
    # `copy_selection` copies the active selection to the clipboard. A backend that can't select or
    # copy natively (a single-touch / coordinate-only backend) raises UnsupportedAction rather than
    # faking it, mirroring the multi-touch gestures — codegen→XCUITest is the iOS path.
    def delete_text(self, count: int) -> None: ...
    def select_all(self) -> None: ...
    def copy_selection(self) -> None: ...
    # Set a native `<select>` (resolved by `sel`) to the option whose value is `option`. Web-only:
    # a `<select>` has no native counterpart on iOS / Android, so those backends raise
    # UnsupportedAction (BE-0191).
    def select_option(self, sel: Selector, option: str) -> None: ...
    # Move the picker wheel resolved by `sel` to the row whose value is `value` (BE-0356). iOS-only:
    # a wheel exposes no addressable row, so only XCUITest's `adjust(toPickerWheelValue:)` can land
    # on one deterministically; a backend without the PICKER_WHEEL capability raises
    # UnsupportedAction. A value the wheel does not carry raises ElementNotFound rather than leaving
    # the wheel wherever it happened to stop.
    def set_picker_value(self, sel: Selector, value: str) -> None: ...
    # Pick the grid cells at `indices` from an already-open `PHPickerViewController`, then tap the
    # picker's confirm control. A backend without SELECT_PHOTOS raises UnsupportedAction; on the one
    # that has it, `capabilities_for_run` has already dropped the token on an Apple Silicon
    # Simulator, where the grid's cells cannot be tapped reliably (roadmap item) — this method's own
    # actuation assumes that gate already ran.
    def select_photos(self, indices: list[int], *, timeout: float) -> None: ...
    # Tap a button on an out-of-process iOS SpringBoard permission prompt (BE-0316), resolving `sel`
    # (label-based only) against the alert's buttons within `timeout`. A backend without the
    # HANDLE_SYSTEM_ALERT capability raises UnsupportedAction; preflight (capability_preflight.py)
    # rejects the scenario before any device work, so this raise is only the mid-run backstop.
    def handle_system_alert(self, sel: Selector, timeout: float) -> None: ...
    # A single, non-blocking read of the SpringBoard alert's button labels — [] when no alert is up
    # (BE-0315). The reactive `systemAlertHandling` guard polls this to learn whether a prompt is showing
    # and which buttons it offers, then taps a policy-named one via `handle_system_alert`. It shares
    # the HANDLE_SYSTEM_ALERT capability (a backend without it returns []), so it never adds a route
    # of its own — the query is BE-0316's `/systemAlert/query`, read here without the tap.
    def system_alert_labels(self) -> list[str]: ...
    # Dismiss a blocking TipKit tip if one is up; True when one was dismissed, False when none was
    # showing. Which node identifies a tip is the driver's business — the caller gets a boolean, so
    # the orchestrator's guard stays free of any iOS-specific identifier. Gated on
    # HANDLE_TIPKIT_TIP: a backend without it returns False rather than raising, since both callers
    # (the post-failure retry and the mid-wait gate) run opportunistically, where "no tip here" and
    # "this backend has no tips" call for the same no-op. `tree`, when given, is a snapshot the
    # caller already holds: the mid-wait gate asks on every poll tick, so letting it answer "no tip"
    # off the poll's own tree keeps the common case free instead of doubling the wait's query load.
    def dismiss_blocking_tip(self, tree: list[Element] | None = None) -> bool: ...
    # A single, non-blocking read of a foreground notification banner's own frame — `None` when none
    # is showing (BE-0416). Measured to enumerate at most one banner (iOS coalesces concurrent ones),
    # in SpringBoard's own coordinate space. Gated on HANDLE_NOTIFICATION_BANNER: a backend without
    # it returns `None` rather than raising, the same opportunistic no-op `dismiss_blocking_tip`
    # follows, since the proactive sweep that reads this runs on every step regardless of backend.
    def notification_banner_frame(self) -> Frame | None: ...
    # Single-shot by contract (BE-0118): whether `sel` matches the *current* screen,
    # checked once. A backend never loops here — the shared `wait_until` owns the
    # deadline poll, so a caller's timeout means the same real seconds on every backend.
    def wait_for(self, sel: Selector) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...
    # Activate the app named by `bundle_id` — installed but not yet running, and never launched by
    # the test target — and make it the target of every method above until a matching
    # `leave_app()`. Nests: entering a second app before leaving the first pushes onto
    # it, and `leave_app` pops back to it, not past it. A backend without the APP_CONTEXT
    # capability raises UnsupportedAction; preflight rejects the scenario before any device work,
    # so this raise is only the mid-run backstop, mirroring `handle_system_alert`. `bundle_id`
    # must name an app already installed on the device: an app that is slow to foreground (a cold
    # launch, a permission prompt) raises ElementNotFound once the bounded poll gives up, but a
    # bundle id that is not installed at all is not guaranteed to fail this cleanly — confirmed on
    # real hardware to leave the runner unresponsive instead, because the handoff from the
    # currently-foreground app never completes. Not fixed here; see the roadmap item's own Scope.
    def enter_app(self, bundle_id: str) -> None: ...
    # Leave the most recently entered app and re-activate the one beneath it — the test target
    # itself, if this is the outermost `leave_app()`.
    def leave_app(self) -> None: ...

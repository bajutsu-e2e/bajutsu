"""In-memory fake driver implementing the Driver Protocol.

Lets the orchestrator (the Tier2 runner) be tested without a Simulator. The
`react` callback scripts "the screen changes in response to an action".
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from bajutsu.drivers import base
from bajutsu.drivers.actuation import Actuation, ActuationLog, Drained
from bajutsu.evidence.network import NetworkExchange, ScreenTransition

# Hook that mutates state in response to an action: react(driver, kind, arg)
React = Callable[["FakeDriver", str, object], None]

# The fake's seeded frames belong to no real device's space, so the unit it stamps on a record is an
# arbitrary but fixed choice; leaving it empty would make this the one backend whose records cannot be
# read uniformly with the rest.
_UNIT = "point"


class FakeNetworkCollector:
    """A deterministic in-process `network.Collector` over a fixed exchange list (BE-0020 tests).

    Real test data, not a behavior mock: it just replays the exchanges it was seeded with, so a
    network-capable fallback can be exercised end to end on the Linux gate without a device.
    """

    def __init__(self, exchanges: list[NetworkExchange]) -> None:
        now = time.monotonic()
        self._items: list[tuple[NetworkExchange, float]] = [(ex, now) for ex in exchanges]

    def snapshot(self) -> list[NetworkExchange]:
        return [ex for ex, _ in self._items]

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        return list(self._items)

    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]:
        return []  # the fake driver seeds no screen-transition events (BE-0310)

    def clear(self) -> None:
        self._items.clear()

    def stop(self) -> None:
        pass  # nothing to release


class FakeDriver:
    """In-memory Driver implementation backing the runner's tests."""

    name = "fake"

    def __init__(
        self,
        screen: Sequence[base.Element] | None = None,
        react: React | None = None,
        exchanges: Sequence[NetworkExchange] | None = None,
        viewport: base.Point | None = None,
    ) -> None:
        self.screen: list[base.Element] = list(screen) if screen is not None else []
        # A minimal scrollable-viewport model (BE-0326): given a `viewport`, `screen` holds elements
        # in content coordinates, `scroll` pans a clamped offset, and `query()` reports frames
        # translated by it — off-screen elements stay in the tree (web-like) with out-of-viewport
        # centers. This makes the `scroll` handler's bounded loop and its center-in-viewport stop
        # condition testable on the fast gate. Left None, the fake is the plain record-and-react
        # driver, and `scroll` only logs the gesture for `react` to script the screen change.
        self._viewport = viewport
        self._scroll_offset: base.Point = (0.0, 0.0)
        # The SpringBoard alert buttons `handle_system_alert` resolves over (BE-0316); tests seed
        # this to stand in for the out-of-process prompt the real backend queries on-device.
        self.system_alert_buttons: list[base.Element] = []
        self._react = react
        self.actions: list[tuple[str, object]] = []  # log of performed actions
        # The concrete actuations this driver performed, drained per step by the run loop. The fake
        # chooses its own touch points (its device is memory), so it records real coordinates rather
        # than stubs — which is what lets the deterministic suite assert exact geometry with no device.
        self._actuations = ActuationLog()
        # When given (even empty), this fake is a network-capable evidence provider (BE-0020): it
        # advertises NETWORK and serves these exchanges via network_collector(). None = no network.
        self._exchanges: list[NetworkExchange] | None = (
            list(exchanges) if exchanges is not None else None
        )

    # --- Driver Protocol ---

    def query(self) -> list[base.Element]:
        # Whatever `screen` was seeded with is reported back unchanged apart from the scroll
        # translation below — including `nativeZ` (BE-0355), which the fake never invents and never
        # drops, so a test can script a stacking order the fast gate can exercise with no device.
        if self._viewport is None:
            return list(self.screen)
        # Scrollable mode: report every element with its frame translated by the scroll offset, so a
        # scrolled-away element keeps its tree entry but reports an out-of-viewport center.
        ox, oy = self._scroll_offset
        return [
            {
                **el,
                "frame": (el["frame"][0] - ox, el["frame"][1] - oy, el["frame"][2], el["frame"][3]),
            }
            for el in self.screen
        ]

    def _check_tappable(self, sel: base.Selector) -> base.Element:
        """Resolve `sel` and raise `ElementNotTappable` if it is covered, per `is_tappable`.

        Shared by `tap` / `double_tap` / `long_press` — a real backend's tap path enforces this
        same check before acting, so a scripted occlusion (seeded in `screen`, or moved into place
        by a `react` callback / driver subclass) must be enforced here too. Without this, the
        orchestrator's `_tap_with_recovery` loop would have nothing to recover from against this
        driver, since only `is_tappable` would ever report the occlusion.
        """
        target = base.resolve_unique(self.screen, sel)
        base.raise_if_covered(self.screen, target, sel)
        return target

    def tap(self, sel: base.Selector) -> None:
        # Like a real semantic tap, require a unique match (ambiguous/not-found -> SelectorError).
        self._log_target("tap", self._check_tappable(sel))
        self._record("tap", sel)

    def is_tappable(self, sel: base.Selector) -> bool:
        # Reuses the same document-order proxy adb uses (`topmost_at_point`), directly over
        # `self.screen` — the same tree `tap` itself resolves against — so the orchestrator's
        # scroll-recovery loop is exercisable on the fast gate without a real device or emulator.
        try:
            target = base.resolve_unique(self.screen, sel)
        except base.ElementNotFound:
            return False
        return (
            base.topmost_at_point(self.screen, base.frame_center(target["frame"]), target) is None
        )

    def tap_point(self, p: base.Point) -> None:
        self._actuations.record(Actuation(gesture="tap", via="coordinate", unit=_UNIT, points=(p,)))
        self._record("tap_point", p)

    def double_tap(self, sel: base.Selector) -> None:
        self._log_target("doubleTap", self._check_tappable(sel))
        self._record("double_tap", sel)

    def long_press(self, sel: base.Selector, duration: float) -> None:
        self._log_target("longPress", self._check_tappable(sel), duration_s=duration)
        self._record("long_press", (sel, duration))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._actuations.record(
            Actuation(gesture="swipe", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        self._record("swipe", (frm, to))

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # Recorded before the pan: `frm`/`to` are the caller's coordinates, so they are already in the
        # space `query()` reports, and the offset this call is about to move must not shift them.
        self._actuations.record(
            Actuation(gesture="scroll", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        if self._viewport is not None:
            # Pan the offset by the gesture's travel (content moves opposite the finger), clamped to
            # the content bounds — so once the region has bottomed out the offset (and thus every
            # reported frame) stops changing, which is how the handler detects end-of-content.
            vw, vh = self._viewport
            cw = max((el["frame"][0] + el["frame"][2] for el in self.screen), default=0.0)
            ch = max((el["frame"][1] + el["frame"][3] for el in self.screen), default=0.0)
            ox, oy = self._scroll_offset
            ox = min(max(ox + (frm[0] - to[0]), 0.0), max(0.0, cw - vw))
            oy = min(max(oy + (frm[1] - to[1]), 0.0), max(0.0, ch - vh))
            self._scroll_offset = (ox, oy)
        self._record("scroll", (frm, to))

    def viewport(self) -> base.Point:
        # Implements ViewportProvider (BE-0326): in scrollable mode the translated tree's content
        # extent overshoots the viewport (as a web DOM tree does), so report the model's viewport;
        # in plain mode every element is on-screen, so the screen extent *is* the viewport — the
        # same value the handler's `screen_size_from_elements` fallback would compute.
        if self._viewport is not None:
            return self._viewport
        w = max((el["frame"][0] + el["frame"][2] for el in self.screen), default=0.0)
        h = max((el["frame"][1] + el["frame"][3] for el in self.screen), default=0.0)
        return (w, h)

    def back(self) -> None:
        self._actuations.record(Actuation(gesture="back", via="key", unit=_UNIT))
        self._record("back", None)

    def pinch(self, sel: base.Selector, scale: float) -> None:
        self._log_target("pinch", base.resolve_unique(self.screen, sel), scale=scale)
        self._record("pinch", (sel, scale))

    def rotate(self, sel: base.Selector, radians: float) -> None:
        self._log_target("rotate", base.resolve_unique(self.screen, sel), radians=radians)
        self._record("rotate", (sel, radians))

    def type_text(self, text: str) -> None:
        # `text` is deliberately absent from the record — not even its length (see `actuation.py`).
        self._actuations.record(Actuation(gesture="typeText", via="focused", unit=_UNIT))
        self._record("type", text)

    def delete_text(self, count: int) -> None:
        self._actuations.record(Actuation(gesture="deleteText", via="focused", unit=_UNIT))
        self._record("delete_text", count)

    def select_all(self) -> None:
        self._actuations.record(Actuation(gesture="selectAll", via="focused", unit=_UNIT))
        self._record("select_all", None)

    def copy_selection(self) -> None:
        self._actuations.record(Actuation(gesture="copy", via="focused", unit=_UNIT))
        self._record("copy_selection", None)

    def select_option(self, sel: base.Selector, option: str) -> None:
        # Like a real driver, require a unique match; state changes are scripted via `react`.
        # `option` never reaches the record (it can hold a resolved secret).
        self._log_target("selectOption", base.resolve_unique(self.screen, sel))
        self._record("select_option", (sel, option))

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
        # Resolve `sel` over the seeded alert buttons with the same discipline the real backend uses
        # (BE-0316): zero → ElementNotFound, ambiguous → AmbiguousSelector, `index` picks the nth.
        if not self.system_alert_buttons:
            raise base.ElementNotFound(f"no system alert appeared within {timeout}s: {sel!r}")
        button = base.resolve_unique(self.system_alert_buttons, sel)
        # Handle-based like the real backend, and out of the app's own coordinate space, so no point.
        self._actuations.record(
            Actuation(gesture="systemAlert", via="handle", unit=_UNIT, target=button["identifier"])
        )
        self._record("handle_system_alert", (sel, timeout))

    def system_alert_labels(self) -> list[str]:
        return [label for b in self.system_alert_buttons if (label := b["label"])]

    def wait_for(self, sel: base.Selector) -> bool:
        return len(base.find_all(self.screen, sel)) >= 1

    def screenshot(self, path: str) -> None:
        self.actions.append(("screenshot", path))

    # A deliberately rich set (semanticTap / conditionWait / multiTouch / selectOption) so tests
    # can exercise those paths. Class constant so the preflight (BE-0082) reads it without
    # constructing a driver.
    CAPABILITIES = frozenset(
        {
            base.Capability.QUERY,
            base.Capability.SEMANTIC_TAP,
            base.Capability.CONDITION_WAIT,
            base.Capability.SCREENSHOT,
            base.Capability.ELEMENTS,
            base.Capability.MULTI_TOUCH,
            base.Capability.SELECT_OPTION,
            base.Capability.TEXT_SELECTION,
            base.Capability.HANDLE_SYSTEM_ALERT,
        }
    )

    def capabilities(self) -> set[str]:
        # A network-seeded fake also advertises NETWORK (instance-level), so it can stand in as a
        # read-only evidence provider; the class constant stays network-free for capabilities_for.
        caps = set(self.CAPABILITIES)
        if self._exchanges is not None:
            caps.add(base.Capability.NETWORK)
        return caps

    def network_collector(self, mocks: list[object] | None = None) -> FakeNetworkCollector:
        """A deterministic collector over the seeded exchanges (read-only evidence; BE-0020)."""
        return FakeNetworkCollector(self._exchanges or [])

    def drain_actuations(self) -> Drained:
        """The concrete actuations performed since the last drain (`ActuationReporter`)."""
        return self._actuations.drain()

    # --- internals ---

    def _log_target(
        self,
        gesture: str,
        el: base.Element,
        *,
        duration_s: float | None = None,
        scale: float | None = None,
        radians: float | None = None,
    ) -> None:
        """Record a gesture aimed at `el`, at the point the fake would touch.

        The frame comes from `query()` space — translated by the scroll offset in scrollable mode —
        while resolution runs against the untranslated `self.screen`, so a recorded point means the
        same thing as the coordinates the orchestrator hands `swipe` / `scroll`.
        """
        frame = self._visible_frame(el["frame"])
        self._actuations.record(
            Actuation(
                gesture=gesture,
                via="coordinate",
                unit=_UNIT,
                points=(base.frame_center(frame),),
                frame=frame,
                target=el["identifier"],
                duration_s=duration_s,
                scale=scale,
                radians=radians,
            )
        )

    def _visible_frame(self, frame: base.Frame) -> base.Frame:
        if self._viewport is None:
            return frame
        ox, oy = self._scroll_offset
        x, y, w, h = frame
        return (x - ox, y - oy, w, h)

    def _record(self, kind: str, arg: object) -> None:
        self.actions.append((kind, arg))
        if self._react is not None:
            self._react(self, kind, arg)

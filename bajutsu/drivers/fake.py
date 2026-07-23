"""In-memory fake driver implementing the Driver Protocol.

Lets the orchestrator (the Tier2 runner) be tested without a Simulator. The
`react` callback scripts "the screen changes in response to an action".
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from bajutsu.drivers import base
from bajutsu.evidence.network import NetworkExchange, ScreenTransition

# Hook that mutates state in response to an action: react(driver, kind, arg)
React = Callable[["FakeDriver", str, object], None]


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
    ) -> None:
        self.screen: list[base.Element] = list(screen) if screen is not None else []
        # The SpringBoard alert buttons `handle_system_alert` resolves over (BE-0316); tests seed
        # this to stand in for the out-of-process prompt the real backend queries on-device.
        self.system_alert_buttons: list[base.Element] = []
        self._react = react
        self.actions: list[tuple[str, object]] = []  # log of performed actions
        # When given (even empty), this fake is a network-capable evidence provider (BE-0020): it
        # advertises NETWORK and serves these exchanges via network_collector(). None = no network.
        self._exchanges: list[NetworkExchange] | None = (
            list(exchanges) if exchanges is not None else None
        )

    # --- Driver Protocol ---

    def query(self) -> list[base.Element]:
        return list(self.screen)

    def tap(self, sel: base.Selector) -> None:
        # Like a real semantic tap, require a unique match (ambiguous/not-found -> SelectorError).
        base.resolve_unique(self.screen, sel)
        self._record("tap", sel)

    def tap_point(self, p: base.Point) -> None:
        self._record("tap_point", p)

    def double_tap(self, sel: base.Selector) -> None:
        base.resolve_unique(self.screen, sel)
        self._record("double_tap", sel)

    def long_press(self, sel: base.Selector, duration: float) -> None:
        base.resolve_unique(self.screen, sel)
        self._record("long_press", (sel, duration))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._record("swipe", (frm, to))

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        self._record("scroll", (frm, to))

    def back(self) -> None:
        self._record("back", None)

    def pinch(self, sel: base.Selector, scale: float) -> None:
        base.resolve_unique(self.screen, sel)
        self._record("pinch", (sel, scale))

    def rotate(self, sel: base.Selector, radians: float) -> None:
        base.resolve_unique(self.screen, sel)
        self._record("rotate", (sel, radians))

    def type_text(self, text: str) -> None:
        self._record("type", text)

    def delete_text(self, count: int) -> None:
        self._record("delete_text", count)

    def select_all(self) -> None:
        self._record("select_all", None)

    def copy_selection(self) -> None:
        self._record("copy_selection", None)

    def select_option(self, sel: base.Selector, option: str) -> None:
        # Like a real driver, require a unique match; state changes are scripted via `react`.
        base.resolve_unique(self.screen, sel)
        self._record("select_option", (sel, option))

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
        # Resolve `sel` over the seeded alert buttons with the same discipline the real backend uses
        # (BE-0316): zero → ElementNotFound, ambiguous → AmbiguousSelector, `index` picks the nth.
        if not self.system_alert_buttons:
            raise base.ElementNotFound(f"no system alert appeared within {timeout}s: {sel!r}")
        base.resolve_unique(self.system_alert_buttons, sel)
        self._record("handle_system_alert", (sel, timeout))

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

    # --- internals ---

    def _record(self, kind: str, arg: object) -> None:
        self.actions.append((kind, arg))
        if self._react is not None:
            self._react(self, kind, arg)

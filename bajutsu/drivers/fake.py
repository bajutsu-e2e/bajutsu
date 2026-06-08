"""In-memory fake driver implementing the Driver Protocol.

Lets the orchestrator (the Tier2 runner) be tested without a Simulator. The
`react` callback scripts "the screen changes in response to an action".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from bajutsu.drivers import base

# Hook that mutates state in response to an action: react(driver, kind, arg)
React = Callable[["FakeDriver", str, object], None]


class FakeDriver:
    name = "fake"

    def __init__(
        self,
        screen: Sequence[base.Element] | None = None,
        react: React | None = None,
    ) -> None:
        self.screen: list[base.Element] = list(screen) if screen is not None else []
        self._react = react
        self.actions: list[tuple[str, object]] = []  # log of performed actions

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

    def pinch(self, sel: base.Selector, scale: float) -> None:
        base.resolve_unique(self.screen, sel)
        self._record("pinch", (sel, scale))

    def rotate(self, sel: base.Selector, radians: float) -> None:
        base.resolve_unique(self.screen, sel)
        self._record("rotate", (sel, radians))

    def type_text(self, text: str) -> None:
        self._record("type", text)

    def wait_for(self, sel: base.Selector, timeout: float) -> bool:
        return len(base.find_all(self.screen, sel)) >= 1

    def screenshot(self, path: str) -> None:
        self.actions.append(("screenshot", path))

    def capabilities(self) -> set[str]:
        return {
            base.Capability.QUERY,
            base.Capability.SEMANTIC_TAP,
            base.Capability.CONDITION_WAIT,
            base.Capability.SCREENSHOT,
            base.Capability.ELEMENTS,
            base.Capability.MULTI_TOUCH,
        }

    # --- internals ---

    def _record(self, kind: str, arg: object) -> None:
        self.actions.append((kind, arg))
        if self._react is not None:
            self._react(self, kind, arg)

"""インメモリの Fake ドライバ（DESIGN.md §5 の Driver を実装）。

Simulator 無しで orchestrator（§3.1 の Tier2 ランナー）をテストするための backend。
`react` コールバックで「操作に応じて画面が変わる」挙動をスクリプトできる。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from simpilot.drivers import base

# 操作に応じて状態を変えるフック: react(driver, kind, arg)
React = Callable[["FakeDriver", str, object], None]


class FakeDriver:
    def __init__(
        self,
        screen: Sequence[base.Element] | None = None,
        react: React | None = None,
    ) -> None:
        self.screen: list[base.Element] = list(screen) if screen is not None else []
        self._react = react
        self.actions: list[tuple[str, object]] = []  # 実行された操作のログ

    # --- Driver Protocol（§5）---

    def query(self) -> list[base.Element]:
        return list(self.screen)

    def tap(self, sel: base.Selector) -> None:
        # 実 backend の semantic tap と同じく一意解決を要求（曖昧/不在は SelectorError）。
        base.resolve_unique(self.screen, sel)
        self._record("tap", sel)

    def long_press(self, sel: base.Selector, duration: float) -> None:
        base.resolve_unique(self.screen, sel)
        self._record("long_press", (sel, duration))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._record("swipe", (frm, to))

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
        }

    # --- 内部 ---

    def _record(self, kind: str, arg: object) -> None:
        self.actions.append((kind, arg))
        if self._react is not None:
            self._react(self, kind, arg)

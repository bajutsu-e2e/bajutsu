"""The slice of Playwright's page the driver uses, narrow enough for a fake to satisfy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from ._keyboard import _Keyboard
from ._mouse import _Mouse


class _Page(Protocol):
    # Read-only members: the driver only ever calls through them, never rebinds them. A mutable
    # attribute would be invariant, which would reject any page whose own handles are typed more
    # precisely than the protocol (BE-0388).
    @property
    def mouse(self) -> _Mouse: ...

    @property
    def keyboard(self) -> _Keyboard: ...

    def evaluate(self, expression: str) -> Any:
        pass

    def goto(self, url: str) -> object:
        pass

    def go_back(self) -> object:
        pass

    def screenshot(self, *, path: str, timeout: float | None = None) -> object:
        pass

    def wait_for_load_state(
        self, state: Literal["domcontentloaded"], *, timeout: float | None = None
    ) -> object:
        pass

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        pass

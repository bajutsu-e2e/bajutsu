"""Whether a text selection is live, tracked across a run's steps (BE-0265)."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.drivers.base import Selector


@dataclass
class SelectionState:
    """Whether a text selection is currently live, tracked across a run's steps (BE-0265).

    `copy` acts on the selection a prior `select` established; no backend exposes selection as
    queryable state, so this is kept Bajutsu-side and uniform across backends: `select` establishes
    it, `copy` reads it without clearing (one selection can be copied more than once), and every
    other *action* invalidates it. `wait` / `assert` are conditions handled in the run loop, never
    routed through the action dispatcher, so they leave a standing selection intact — a `select`,
    then a `wait` for a menu, then `copy` is a valid sequence. A `copy` with no live selection fails
    the step rather than silently copying nothing.

    The state also records the field the `select` named, because iOS copies by raising the edit
    menu on that field again rather than through a shortcut on whatever has focus (BE-0493).

    The transitions live on this type (not in the caller) so the contract stays in one place.
    """

    sel: Selector | None = None

    def establish(self, sel: Selector) -> None:
        self.sel = sel

    def invalidate(self) -> None:
        self.sel = None

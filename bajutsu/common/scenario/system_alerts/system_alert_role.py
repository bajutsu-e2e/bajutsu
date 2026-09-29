"""A prompt button named by its position, not its text (BE-0445)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SystemAlertRole:
    """Which button of a SpringBoard prompt carries a choice, by its place among the prompt's buttons.

    The rule BE-0445 measured to hold across Simulator languages: SpringBoard lists a permission
    prompt's buttons in the same order whatever the language it renders them in. `count` is part of
    the rule rather than a detail of it — an alert offering any other number of buttons is not the
    prompt the rule describes, so the rule names no button on it instead of tapping by position on
    an alert it was never measured against.
    """

    ordinal: int
    count: int

    def pick(self, buttons: Sequence[str]) -> str | None:
        """The label of the button this rule names on an alert offering `buttons`, if it names one."""
        return buttons[self.ordinal] if len(buttons) == self.count else None

    def describe(self) -> str:
        """The rule in the words a report shows, e.g. "button 2 of 2"."""
        return f"button {self.ordinal + 1} of {self.count}"

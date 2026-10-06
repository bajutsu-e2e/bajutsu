"""The monotonic step index the recursive step loop shares (BE-0172)."""

from __future__ import annotations


class _StepCounter:
    """A monotonically increasing step index shared across the recursive step loop (BE-0172).

    A named replacement for the former ``step_counter = [0]`` closure smuggle: ``take()`` returns
    the current index and advances, so nested ``for_each`` / ``web`` groups keep unique, ordered
    indices without a boxed list.
    """

    def __init__(self) -> None:
        self._next = 0

    def take(self) -> int:
        idx = self._next
        self._next += 1
        return idx

    def peek(self) -> int:
        """The index the next `take()` will return, without advancing."""
        return self._next

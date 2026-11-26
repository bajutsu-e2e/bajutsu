"""The child-process seam an interval capture is driven through."""

from __future__ import annotations

from typing import Protocol


class Proc(Protocol):
    """A running child process that can be signalled and waited on."""

    def stop(self, sig: int, timeout: float) -> None: ...

    def await_stderr(self, needle: str, timeout: float) -> float | None: ...

    def await_stdout(self, timeout: float) -> float | None:
        """Wait for the first byte on stdout; the instant it appeared, or None."""

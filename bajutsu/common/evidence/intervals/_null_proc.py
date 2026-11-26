"""The no-op process, so constructing an interval has no side effects of its own."""

from __future__ import annotations


class _NullProc:
    """A no-op process (the default so constructing an Interval has no side effects)."""

    def stop(self, sig: int, timeout: float) -> None:  # noqa: ARG002  # Proc shape
        return None

    def await_stderr(self, needle: str, timeout: float) -> float | None:  # noqa: ARG002  # Proc shape
        return None

    def await_stdout(self, timeout: float) -> float | None:  # noqa: ARG002  # Proc shape
        return None

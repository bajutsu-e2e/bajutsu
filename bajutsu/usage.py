"""Token-usage accounting for the AI-backed paths.

Every Anthropic call in the tool — the authoring agent, the alert locator, the triage
agent — reports its response `usage` here, and the CLI commands read the running total to
show how many tokens a feature consumed. This is reporting only: it never touches the
deterministic pass/fail judgement, so recording is best-effort and must never raise.

The tracker is a process-global accumulator guarded by a lock: `run --workers N` shares one
alert locator across threads, so several threads can record concurrently. Commands take a
`snapshot()` before and after their work and show the difference — so a long-lived process
(e.g. the web server) reports per-invocation totals, not a process-lifetime sum.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    """An immutable snapshot of cumulative token counts across AI calls.

    `input_tokens` is the uncached input (the Anthropic API reports cache-written and
    cache-read input as the separate `cache_*` buckets), so the billed total is the sum of
    all four counts.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )

    def __sub__(self, other: TokenUsage) -> TokenUsage:
        """The usage accrued between two snapshots (`after - before`)."""
        return TokenUsage(
            input_tokens=self.input_tokens - other.input_tokens,
            output_tokens=self.output_tokens - other.output_tokens,
            cache_write_tokens=self.cache_write_tokens - other.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens - other.cache_read_tokens,
            calls=self.calls - other.calls,
        )

    def render(self) -> str:
        """A one-line human summary, e.g.
        `AI usage: 1,234 tokens over 3 calls (900 in, 300 out; cache 20 write, 14 read)`."""
        cache = ""
        if self.cache_write_tokens or self.cache_read_tokens:
            cache = f"; cache {self.cache_write_tokens:,} write, {self.cache_read_tokens:,} read"
        plural = "" if self.calls == 1 else "s"
        return (
            f"AI usage: {self.total_tokens:,} tokens over {self.calls} call{plural} "
            f"({self.input_tokens:,} in, {self.output_tokens:,} out{cache})"
        )


def _int(value: Any) -> int:
    """A token count coerced to a non-negative int; anything unexpected counts as zero."""
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


class _Accumulator:
    """Thread-safe running total of token counts across AI responses."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._input = 0
        self._output = 0
        self._cache_write = 0
        self._cache_read = 0
        self._calls = 0

    def record(self, usage: Any) -> None:
        """Add one response's `usage` to the running total. Best-effort: a `None` usage (a
        mocked client, or an SDK that omitted it) is ignored, and a missing field counts as
        zero — recording never raises and never affects pass/fail."""
        if usage is None:
            return
        with self._lock:
            self._input += _int(getattr(usage, "input_tokens", 0))
            self._output += _int(getattr(usage, "output_tokens", 0))
            self._cache_write += _int(getattr(usage, "cache_creation_input_tokens", 0))
            self._cache_read += _int(getattr(usage, "cache_read_input_tokens", 0))
            self._calls += 1

    def snapshot(self) -> TokenUsage:
        with self._lock:
            return TokenUsage(
                self._input, self._output, self._cache_write, self._cache_read, self._calls
            )


_TRACKER = _Accumulator()


def record(usage: Any) -> None:
    """Record one Anthropic response's `usage` into the process-global tracker."""
    _TRACKER.record(usage)


def snapshot() -> TokenUsage:
    """The cumulative usage so far. Take one before and after a feature, then subtract to get
    what that feature consumed (`usage.snapshot() - before`)."""
    return _TRACKER.snapshot()

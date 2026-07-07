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

import contextlib
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

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Field-wise sum of two snapshots (the per-category accumulator folds each call into its bucket)."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            calls=self.calls + other.calls,
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


def _field(usage: Any, name: str) -> int:
    """One token field, read from an SDK usage object *or* a plain dict (the Claude Code adapter's
    envelope reports usage as a dict — BE-0176 — so attribute-only access would count it as zero)."""
    raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    return _int(raw)


def of(usage: Any) -> TokenUsage:
    """One response's counts as a `TokenUsage` (``calls=1`` when usage is present), for per-call
    display. Best-effort like `record`: a ``None`` usage yields an empty (zero) snapshot."""
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=_field(usage, "input_tokens"),
        output_tokens=_field(usage, "output_tokens"),
        cache_write_tokens=_field(usage, "cache_creation_input_tokens"),
        cache_read_tokens=_field(usage, "cache_read_input_tokens"),
        calls=1,
    )


# The call site a token count is attributed to, so the record report reads as a breakdown rather
# than one opaque total (BE-0194 §4). The record path spends in three places; everything else (run's
# alert guard, triage, crawl, enrich) falls in the default `other` bucket — reporting only, never on
# the pass/fail path.
CATEGORY_PLAN = "plan"  # the up-front `plan` call
CATEGORY_ACTION = "next_action"  # the per-turn `next_action` calls (the element tree + screenshot)
CATEGORY_ALERT = "alert-guard"  # the alert-guard vision calls
CATEGORY_OTHER = "other"  # any uncategorized AI call


class _Accumulator:
    """Thread-safe running total of token counts across AI responses, kept per category (BE-0194).

    The categories partition every recorded call (each lands in exactly one), so their sum is the
    running total — `snapshot()` folds them back together for callers that want just the total.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._by_category: dict[str, TokenUsage] = {}

    def record(self, usage: Any, category: str = CATEGORY_OTHER) -> None:
        """Add one response's `usage` to `category`'s running total. Best-effort: a `None` usage (a
        mocked client, or an SDK that omitted it) is ignored, and a missing field counts as
        zero — recording never raises and never affects pass/fail."""
        if usage is None:
            return
        one = of(usage)
        with self._lock:
            self._by_category[category] = self._by_category.get(category, TokenUsage()) + one

    def snapshot(self) -> TokenUsage:
        with self._lock:
            return sum(self._by_category.values(), TokenUsage())

    def snapshot_by_category(self) -> dict[str, TokenUsage]:
        with self._lock:
            return dict(self._by_category)


_TRACKER = _Accumulator()


def record(
    usage: Any,
    category: str = CATEGORY_OTHER,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Record one provider response's `usage` into the process-global tracker under `category`.

    Provider-agnostic (BE-0104): the `usage` object comes from whichever backend answered — the
    Anthropic SDK, Bedrock, or the `claude-code` CLI's dict envelope. When a ledger is configured
    (BE-0196), also append one attributed, priced event; *provider* and *model* name what produced
    the tokens. Ledger emission is best-effort and never raises — a broken sink must not break the AI
    path — and never touches the deterministic verdict.
    """
    _TRACKER.record(usage, category)
    _emit_ledger_event(usage, provider=provider, model=model)


def _emit_ledger_event(usage: Any, *, provider: str | None, model: str | None) -> None:
    """Forward the usage to the ledger, swallowing anything it raises (reporting only, BE-0196).

    Imported lazily so `bajutsu.usage` stays free of the ledger's import at module load (the ledger
    imports `TokenUsage` from here), and so the deterministic core never pulls it in transitively.
    """
    with contextlib.suppress(Exception):
        from bajutsu import usage_ledger

        usage_ledger.emit(usage, provider=provider, model=model)


def snapshot() -> TokenUsage:
    """The cumulative usage so far. Take one before and after a feature, then subtract to get
    what that feature consumed (`usage.snapshot() - before`)."""
    return _TRACKER.snapshot()


def snapshot_by_category() -> dict[str, TokenUsage]:
    """The cumulative usage so far, split by call-site category (BE-0194 §4). Take one before and
    after a feature and subtract per category to get what each call site consumed."""
    return _TRACKER.snapshot_by_category()


# Categories listed in this order in the breakdown (known ones first, then any extra), so the report
# reads plan → per-turn actions → alert guard rather than dict-insertion order.
_CATEGORY_ORDER = (CATEGORY_PLAN, CATEGORY_ACTION, CATEGORY_ALERT, CATEGORY_OTHER)


def breakdown_lines(before: dict[str, TokenUsage], after: dict[str, TokenUsage]) -> list[str]:
    """Per-category delta lines to print under the one-line total (`before`/`after` from
    `snapshot_by_category`). A category with no spend between the snapshots is omitted, so a normal
    record shows only the buckets it actually used. Reporting only — never on the pass/fail path."""
    ordered = [*_CATEGORY_ORDER, *sorted(set(after) - set(_CATEGORY_ORDER))]
    lines = []
    for category in ordered:
        delta = after.get(category, TokenUsage()) - before.get(category, TokenUsage())
        if delta.calls:
            plural = "" if delta.calls == 1 else "s"
            lines.append(
                f"  {category}: {delta.total_tokens:,} tokens over {delta.calls} call{plural}"
            )
    return lines

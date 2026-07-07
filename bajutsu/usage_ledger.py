"""Attributed, persistent AI usage/cost ledger (BE-0196).

`bajutsu.usage` keeps a flat, in-memory token total that dies with the process. This module makes
that history durable and attributed: one JSONL line per AI call, tagged with what the tokens were
spent on (command, provider, model, scenario, step) and priced in dollars where the provider has
per-token pricing. It is the raw material the usage dashboard reads.

Reporting only — nothing here runs on the deterministic `run` / CI verdict, and recording is
best-effort: `bajutsu.usage.record` calls `emit` inside a swallow-everything guard, so a full disk
never breaks an AI path. Following the operational-logging rules (BE-0055 / BE-0047), the ledger
stores counts, prices, and labels only — never prompt or response content.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from bajutsu.config import AiConfig
from bajutsu.usage import TokenUsage, of

# Bump when the on-disk record shape changes incompatibly; readers key off it to stay
# forward-compatible (an older line is still parseable — see `UsageEvent.from_record`).
LEDGER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Pricing:
    """Per-token rates for one `(provider, model)`, expressed in US dollars per million tokens.

    Per-million (not per-token) because that is how providers publish list prices, so a config
    override reads the same as the vendor's page. `cost` converts back to an absolute dollar figure.
    """

    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_write_usd_per_mtok: float
    cache_read_usd_per_mtok: float

    def cost(self, u: TokenUsage) -> float:
        """The dollar cost of *u* at these rates."""
        return (
            u.input_tokens * self.input_usd_per_mtok
            + u.output_tokens * self.output_usd_per_mtok
            + u.cache_write_tokens * self.cache_write_usd_per_mtok
            + u.cache_read_tokens * self.cache_read_usd_per_mtok
        ) / 1_000_000


# Shipped default list prices (USD per million tokens), keyed by (provider, model *family*). The
# family key matches by substring against the full model id, so version suffixes
# (`claude-sonnet-4-6-2025…`) resolve without an entry per revision. Prices go stale; the config
# `pricing:` block overrides any key, so a correction is a one-line config change, not a code change.
# Subscription providers (`ant`, `claude-code`) have no entries on purpose — cost stays null.
_DEFAULT_PRICING: dict[tuple[str, str], Pricing] = {
    ("api-key", "opus"): Pricing(15.0, 75.0, 18.75, 1.5),
    ("api-key", "sonnet"): Pricing(3.0, 15.0, 3.75, 0.3),
    ("api-key", "haiku"): Pricing(0.8, 4.0, 1.0, 0.08),
    # Bedrock list prices track the direct API for the same model family.
    ("bedrock", "opus"): Pricing(15.0, 75.0, 18.75, 1.5),
    ("bedrock", "sonnet"): Pricing(3.0, 15.0, 3.75, 0.3),
    ("bedrock", "haiku"): Pricing(0.8, 4.0, 1.0, 0.08),
}

PricingTable = dict[tuple[str, str], Pricing]


def default_pricing_table() -> PricingTable:
    """A fresh copy of the shipped default prices (callers may overlay config onto it)."""
    return dict(_DEFAULT_PRICING)


def pricing_table_from_config(config: Mapping[str, Any] | None) -> PricingTable:
    """The effective pricing table: shipped defaults overlaid by a config `pricing:` block.

    Args:
        config: The `ai.pricing` mapping keyed by ``"provider/model"`` with `input` / `output` /
            `cacheWrite` / `cacheRead` rates (USD per million tokens); None uses defaults only.
    """
    table = default_pricing_table()
    for key, rates in (config or {}).items():
        provider, _, model = key.partition("/")
        # A malformed key ("no-slash", "provider/", "/model") must not become a catch-all: an empty
        # model would family-match every model for that provider (`"" in anything` is always true).
        if not provider or not model:
            continue
        table[(provider, model)] = Pricing(
            input_usd_per_mtok=float(rates.get("input", 0.0)),
            output_usd_per_mtok=float(rates.get("output", 0.0)),
            cache_write_usd_per_mtok=float(rates.get("cacheWrite", 0.0)),
            cache_read_usd_per_mtok=float(rates.get("cacheRead", 0.0)),
        )
    return table


def _find_pricing(table: PricingTable, provider: str | None, model: str | None) -> Pricing | None:
    """The rates for *(provider, model)*: an exact key first, then a family-substring match."""
    if provider is None or model is None:
        return None
    exact = table.get((provider, model))
    if exact is not None:
        return exact
    low = model.casefold()
    # Case-insensitive on both sides so a config family like `api-key/Sonnet` still matches
    # `claude-sonnet-…`. `family` is never empty (config overlay skips empty-model keys), but guard
    # anyway — an empty family would substring-match every model and misprice them all.
    return next(
        (
            p
            for (prov, family), p in table.items()
            if prov == provider and family and family.casefold() in low
        ),
        None,
    )


def compute_cost(
    table: PricingTable, provider: str | None, model: str | None, u: TokenUsage
) -> float | None:
    """The dollar cost of *u*, or None when the (provider, model) has no per-token price.

    None is the explicit "unpriced" marker — a subscription provider (`ant` / `claude-code`) or an
    unknown model — so the ledger records tokens without fabricating a dollar figure.
    """
    pricing = _find_pricing(table, provider, model)
    return pricing.cost(u) if pricing is not None else None


@dataclass(frozen=True)
class Attribution:
    """What an AI call's tokens were spent on — the ledger's non-token dimensions."""

    command: str | None = None
    scenario: str | None = None
    step: str | None = None


# Default None (not an `Attribution()` literal) so the ContextVar default is immutable; readers
# coalesce None to the empty attribution via `current_attribution`.
_ATTRIBUTION: ContextVar[Attribution | None] = ContextVar("bajutsu_usage_attribution", default=None)


@contextmanager
def attributed(
    *, command: str | None = None, scenario: str | None = None, step: str | None = None
) -> Iterator[None]:
    """Scope the attribution the ledger reads at `record` time, refining any enclosing scope.

    A CLI command sets `command` (and often `scenario`) at its boundary; a step-level scope adds
    `step` without disturbing the command already bound. An unset argument inherits the outer value,
    so nesting composes rather than clobbers.
    """
    current = current_attribution()
    merged = Attribution(
        command=command if command is not None else current.command,
        scenario=scenario if scenario is not None else current.scenario,
        step=step if step is not None else current.step,
    )
    token = _ATTRIBUTION.set(merged)
    try:
        yield
    finally:
        _ATTRIBUTION.reset(token)


def bind_command(
    command: str | None = None, *, scenario: str | None = None, step: str | None = None
) -> None:
    """Bind attribution at a one-shot boundary (a CLI command) without scoping it.

    Mirrors `oplog.bind_request`: a CLI process runs one command and exits, so the binding needs no
    reset. Use `attributed` (a context manager) where a scope must be entered and left — e.g. `run`
    wraps each scenario's alert guard in `attributed(command="run", scenario=…)` so the binding is
    set inside the runner's worker thread, where the guard's AI call actually happens.
    """
    _ATTRIBUTION.set(Attribution(command=command, scenario=scenario, step=step))


def current_attribution() -> Attribution:
    """The attribution bound by the innermost enclosing `attributed` scope (empty by default)."""
    return _ATTRIBUTION.get() or Attribution()


@dataclass(frozen=True)
class UsageEvent:
    """One AI call's durable record: its attribution, token counts, and computed dollar cost."""

    ts: str  # UTC ISO-8601 timestamp
    command: str | None
    provider: str | None
    model: str | None
    scenario: str | None
    step: str | None
    usage: TokenUsage
    cost: float | None

    def to_record(self) -> dict[str, Any]:
        """The versioned, JSON-serializable dict written as one ledger line."""
        return {
            "v": LEDGER_SCHEMA_VERSION,
            "ts": self.ts,
            "command": self.command,
            "provider": self.provider,
            "model": self.model,
            "scenario": self.scenario,
            "step": self.step,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cache_write_tokens": self.usage.cache_write_tokens,
            "cache_read_tokens": self.usage.cache_read_tokens,
            "calls": self.usage.calls,
            "cost": self.cost,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> UsageEvent:
        """Parse one ledger line. Missing fields degrade gracefully (older/partial lines stay readable)."""
        return cls(
            ts=record["ts"],
            command=record.get("command"),
            provider=record.get("provider"),
            model=record.get("model"),
            scenario=record.get("scenario"),
            step=record.get("step"),
            usage=TokenUsage(
                input_tokens=record.get("input_tokens", 0),
                output_tokens=record.get("output_tokens", 0),
                cache_write_tokens=record.get("cache_write_tokens", 0),
                cache_read_tokens=record.get("cache_read_tokens", 0),
                calls=record.get("calls", 0),
            ),
            cost=record.get("cost"),
        )


class JsonlLedger:
    """An append-only JSONL sink: one line per event, lock-guarded for concurrent `run --workers`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def append(self, event: UsageEvent) -> None:
        """Append one event as a JSON line, creating the parent directory on first write."""
        line = json.dumps(event.to_record(), ensure_ascii=False)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def read_events(path: Path) -> list[UsageEvent]:
    """Every readable event in the ledger at *path* (empty when the file is absent).

    Resilient to a real append-only ledger: blank lines are skipped, and a malformed or partially
    written line (a crash or disk-full mid-append leaves a truncated last line) is skipped rather
    than failing the whole read — one bad line must not hide every other event.
    """
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(UsageEvent.from_record(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue  # truncated / malformed / hand-edited line — skip it, keep the rest
    return events


# The process-active ledger sink and pricing table, installed by the CLI/serve at startup. None
# means "no ledger configured", so `emit` is a no-op and only the in-memory total accrues (unit 5).
_ACTIVE_LEDGER: JsonlLedger | None = None
_ACTIVE_PRICING: PricingTable = {}


def configure(ledger: JsonlLedger | None, pricing: PricingTable) -> None:
    """Install the active ledger sink and pricing table for this process."""
    global _ACTIVE_LEDGER, _ACTIVE_PRICING
    _ACTIVE_LEDGER = ledger
    _ACTIVE_PRICING = pricing


def reset() -> None:
    """Detach the active ledger and unbind attribution (idempotent — test teardown / reconfigure).

    Clears the attribution too so `bind_command` (which sets the contextvar without a scope, for a
    one-shot CLI process) cannot leak a binding from one test into the next in the same worker.
    """
    global _ACTIVE_LEDGER, _ACTIVE_PRICING
    _ACTIVE_LEDGER = None
    _ACTIVE_PRICING = {}
    _ATTRIBUTION.set(None)


# Default ledger location — under the gitignored `runs/` tree, so records accumulate but never land
# in the repo. `ai.usageLedger` overrides the path; an empty string disables persistence.
DEFAULT_LEDGER_PATH = Path("runs") / "usage.jsonl"


def resolve_ledger_path(configured: str | None) -> Path | None:
    """The ledger file for a `usageLedger` config value, or None when persistence is disabled.

    A configured path wins; an unset (None) value falls back to `DEFAULT_LEDGER_PATH`; an explicit
    empty string is the "disabled" marker and yields None. The single place this rule lives, shared
    by the writer (`configure_from_ai_config`) and the serve dashboard reader (BE-0195) so the two
    never resolve the same config differently. The path may be relative — the caller resolves it
    against the cwd the AI paths run in.
    """
    if configured == "":  # explicitly disabled
        return None
    return Path(configured) if configured else DEFAULT_LEDGER_PATH


def configure_from_ai_config(ai: AiConfig | None) -> None:
    """Install the process ledger from a resolved `AiConfig` (called by a CLI/serve AI command).

    The path is `ai.usage_ledger` when set, else `DEFAULT_LEDGER_PATH`; an explicit empty string
    disables persistence. Pricing overlays the config `pricing` block onto the shipped defaults.
    """
    path = resolve_ledger_path(ai.usage_ledger if ai is not None else None)
    ledger = JsonlLedger(path) if path is not None else None
    configure(ledger, pricing_table_from_config(ai.pricing if ai is not None else None))


def emit(raw_usage: Any, *, provider: str | None, model: str | None) -> None:
    """Append one attributed, priced event for *raw_usage* — a no-op when no ledger is configured.

    Called only from `bajutsu.usage.record`, which wraps it in a swallow-everything guard, so this
    stays reporting-only and best-effort: it never influences pass/fail and never propagates.
    """
    ledger = _ACTIVE_LEDGER
    if ledger is None or raw_usage is None:
        return
    one = of(raw_usage)
    attribution = current_attribution()
    event = UsageEvent(
        ts=datetime.now(UTC).isoformat(),
        command=attribution.command,
        provider=provider,
        model=model,
        scenario=attribution.scenario,
        step=attribution.step,
        usage=one,
        cost=compute_cost(_ACTIVE_PRICING, provider, model, one),
    )
    ledger.append(event)

**English** · [日本語](BE-0196-ai-usage-cost-ledger-ja.md)

# BE-0196 — Record AI token usage and cost as an attributed, persistent ledger

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0196](BE-0196-ai-usage-cost-ledger.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0196") |
| Implementing PR | [#783](https://github.com/bajutsu-e2e/bajutsu/pull/783) |
| Topic | AI usage and cost observability |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's AI paths (`record`, `crawl`, `triage --ai`, and `run --apply`) already send every provider
response through `bajutsu/usage.py`, which accumulates a token total in memory. This proposal turns
that ephemeral counter into an **attributed, persistent ledger**. The ledger keeps one durable record
per AI call. Each record is tagged with what the tokens were spent on (command, provider, model,
scenario, step) and priced in dollars where the provider has per-token pricing.

The ledger is the raw material for answering "where does our AI spend go, and which provider and model
is most efficient for which task?" That question motivates smarter, data-driven provider selection.

This item is the recording layer alone. Surfacing the ledger in the serve Web UI is a separate item
(`ai-usage-cost-dashboard`), which consumes the format defined here.

## Motivation

Today `bajutsu/usage.py` answers exactly one question, and only for the lifetime of a single process:
*how many tokens did this invocation use in total?* It prints a one-line
`AI usage: N tokens over M calls` summary to stdout and then forgets everything. Three limitations
follow, and each blocks the optimization the user is after:

- **No attribution.** The total is flat. It cannot say that `crawl` on a large app cost 10× what a
  `record` session did, or that model X on scenario Y is where the tokens go. Without knowing *what*
  the tokens were spent on, a team has no basis for deciding where to economize.
- **No persistence.** The counter lives in process memory and dies with the process. Nothing survives
  across `record` / `triage` / `run` invocations, so no history accumulates and no trend is
  observable.
- **No cost, and no per-provider or per-model breakdown.** `TokenUsage` is a single set of counters,
  regardless of which of the four providers (`api-key` / `bedrock` / `ant` / `claude-code`) or which
  model produced the tokens. It computes no dollar figure at all. The recorded data cannot support
  comparing providers on cost, which is the concrete lever for "use the AI provider more smartly".

The instrumentation is already in the right place. `bajutsu/ai/base.py` passes each backend's `usage`
field through untouched, and every call site already calls `usage.record(...)`. The gap is not
*capturing* the data but *attributing, pricing, and keeping* it. Closing the gap is cheap, because no
call site needs re-instrumenting. Only `bajutsu/usage.py` changes, plus a thin attribution context
around the existing calls.

The item is observability, squarely on the AI authoring/investigation side of the prime directives.
No part of it runs during the deterministic `run` / CI verdict, and nothing in it lets an LLM
influence pass/fail. Any *future* provider routing that reads this ledger must make its choice
deterministically (config- or threshold-based), never by asking an LLM. That routing is out of scope
here, because this item produces the data alone.

## Detailed design

The work comprises the following mutually exclusive, collectively exhaustive units:

1. **A usage event schema.** One record per AI call carries:
   - the token counts already in `TokenUsage` (`input_tokens` / `output_tokens` /
     `cache_write_tokens` / `cache_read_tokens` / `calls`);
   - the attribution dimensions (`command`, `provider`, `model`, `scenario`, `step`/task label);
   - a UTC timestamp;
   - the computed `cost` (nullable; see unit 3).

   The record is a plain, versioned dict, so the on-disk format is forward-compatible and mirrors the
   existing structured-logging style (BE-0055).

2. **Attribution plumbing.** The "what for" dimensions reach the point where `usage.record(...)` is
   called, without re-instrumenting each call site. A context (a `contextvar`-based scope set by the
   CLI command or MCP tool, e.g. `with usage.attributed(command="crawl", scenario=…, step=…):`)
   carries them, and `bajutsu/usage.py` reads it when it records an event. The provider and model come
   from the `AiBackend` in use, so `bajutsu/usage.py` reads them from the backend, and callers do not
   pass them.

3. **Per-provider/per-model cost computation.** A pricing table keyed by `(provider, model)` gives
   input/output (and cache) per-token rates, and applies them when an event is recorded. Pricing is
   per-provider data that changes over time, so it lives in **config** (a `pricing:` block under the
   provider/target config), with a shipped default table and a config override. This is consistent
   with the app-agnostic prime directive. A subscription/OAuth provider with no per-token price
   (`ant`, `claude-code`) records `cost = null` while still recording full token counts. The ledger
   marks these records explicitly instead of guessing a number.

4. **Append-only persistence (recommended: a JSONL ledger).** Each event appends one line to a ledger
   file (default under the gitignored `runs/` tree; path configurable), so records survive process
   exit and accumulate across every AI invocation. Append-only keeps writes simple and crash-safe.
   Aggregation is a read-time concern left to the consumer (the dashboard item). Redaction follows the
   existing operational-logging rules (BE-0055 / BE-0047): the ledger stores counts, prices, and
   labels, never prompt or response content.

5. **Keep the existing in-memory total intact.** `TokenUsage.snapshot()` / `render()` and their
   before/after-diff callers keep working unchanged, and the ledger is additive. The CLI one-line
   summary stays, so nothing regresses for users who do not opt into the ledger.

6. **Tests.** Unit tests cover the schema, the pricing computation (including the `cost = null` path
   for subscription providers), the attribution scope, and the append/read round-trip. All are pure
   and Simulator-free, so they run in the standard gate.

## Alternatives considered

- **SQLite instead of JSONL.** A relational store would make the dashboard's aggregation queries
  easier and cheaper. Rejected as the default: it adds schema-migration overhead and a heavier
  read/write path for what is fundamentally an append-only event stream. JSONL matches the repo's
  lightweight, dependency-light style and the BE-0055 structured-log precedent. If aggregate query
  cost ever becomes the bottleneck, the consuming dashboard can build its own index over the JSONL,
  because an append-only on-disk format does not preclude that.
- **Token counts only, no dollar cost.** This is simpler, and it sidesteps stale pricing tables.
  Rejected, because dollar cost is the most direct signal for provider comparison, which is the stated
  motivation. Token efficiency alone hides that provider A's cheaper-per-token model may still cost
  more for the same task. Pricing lives in overridable config precisely so that fixing a stale default
  takes a one-line change, not a code change.
- **Extend BE-0169's Prometheus `/metrics` endpoint instead of a ledger.** `/metrics` is built for
  operational gauges and counters scraped live (queue depth, run durations). It is not built for a
  durable, per-call, attributed history that a human queries after the fact. The two are
  complementary: this ledger is the historical record, and a per-provider cost counter could later
  also be exported to `/metrics` as a separate, small follow-up.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Usage event schema (versioned record shape)
- [x] Attribution plumbing (contextvar scope around `usage.record`)
- [x] Per-provider/per-model cost computation + config-sourced pricing table
- [x] Append-only JSONL ledger (persistence + redaction)
- [x] Preserve the existing in-memory total and CLI summary
- [x] Tests (schema, pricing incl. `cost = null`, attribution, round-trip)

Log:

- [#783](https://github.com/bajutsu-e2e/bajutsu/pull/783) — Land the ledger: `bajutsu/usage_ledger.py` (versioned `UsageEvent`, `Pricing` table
  with a shipped default overridable by `ai.pricing`, `attributed` contextvar scope, append-only
  `JsonlLedger`), extend `usage.record` to emit best-effort attributed events, add the `ai.pricing`
  / `ai.usageLedger` config, and wire `record` / `crawl` / `triage` / `run` to install the ledger and
  bind attribution (`run` binds per-scenario at the alert guard so it reaches worker threads).

## References

- `bajutsu/usage.py` — the existing in-memory `TokenUsage` accumulator this item extends.
- `bajutsu/ai/base.py`, `bajutsu/ai/registry.py` — the `AiBackend` seam and provider registry
  (`api-key` / `bedrock` / `ant` / `claude-code`) that already pass `usage` through
  ([BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)).
- [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) (Operational logging) — the structured-JSON, secret-redacting logging style this ledger follows.
- [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) (AI data sovereignty) — the redaction rules the ledger inherits.
- [BE-0169](../BE-0169-serve-metrics-observability/BE-0169-serve-metrics-observability.md) (Serve metrics and observability endpoint) — the complementary operational `/metrics` surface.
- `ai-usage-cost-dashboard` — the sibling item that visualizes this ledger in the serve Web UI.

**English** · [日本語](BE-0012-action-capture-record-ja.md)

# BE-0012 — Action-capture record

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0012](BE-0012-action-capture-record.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Track | [Proposals](../../README.md#proposals) |
| Topic | Authoring experience (record / GUI editor) |
<!-- /BE-METADATA -->

## Introduction

Record real user operations on the Simulator (tap / type / swipe) directly into a scenario, without relying on AI. This requires idb event capture or accessibility-event monitoring.

## Motivation

Today the only path from "a thing a human does" to a scenario is the AI record loop (`record.py`): a natural-language goal drives an agent that explores the app and proposes steps. That is powerful for authoring from intent, but it has costs the author cannot always pay — it needs `ANTHROPIC_API_KEY`, it spends LLM (large language model) round-trips, and for a flow the author already knows by heart, narrating it as a goal and waiting for the agent is slower than simply *doing* it. There are also flows easier to demonstrate than to describe (a precise swipe, a multi-field form, an exact tap order). A capture mode that turns real operations on the Simulator directly into scenario steps gives authors a fast, offline, deterministic on-ramp, complementing — not replacing — the AI loop.

## Detailed design

Capture observes the operations a human performs on a booted Simulator (tap / type / swipe) and emits the same `Scenario` (steps + `expect`) the AI loop produces, so everything downstream — `run`, `codegen`, the report — is unchanged. The hard part is determinism: a raw event stream is a sequence of *coordinates*, and the prime directive is that scenarios select by stable `accessibilityIdentifier`, not coordinates. So each captured action is resolved against a fresh `query()` taken at the moment of the action: the tapped point is mapped to the element whose frame contains it, and that element is written as an `id` selector (falling back down the stability ladder to `label` / `traits`, and only as a last resort to a coordinate tap, flagged as a degradation). If the point resolves ambiguously to two elements, capture surfaces the ambiguity for the author to disambiguate rather than guessing — the same "ambiguous fails rather than tapping whatever matched first" rule the runner enforces.

Two sources are candidates for the event stream: idb's own event capture, or accessibility-event monitoring. The proposal stays neutral on which; the resolver and emitter sit behind a small interface so either can feed it, mirroring how the driver normalizes backends. Timing is captured as **condition waits, not fixed sleeps**: when an action lands on a screen that differs from the previous one, capture inserts a `wait` for the first element the next action targets — the same self-sufficiency the AI loop gets from `_settle_step` — so replay never relies on wall-clock timing.

Capture stays app-agnostic: it reads the element tree and config (`apps.<name>` for the target app, its scenarios dir, redaction) exactly as the existing commands do, and writes the authored YAML the same way. It is strictly Tier 1 — capture authors a scenario, and like the AI loop, no part of it enters the deterministic `run` / CI gate. Unlike the AI loop it needs **no API key**, since resolution is purely structural (point-in-frame against `query()`), keeping a fully offline authoring path.

## Alternatives considered

* **Record raw coordinate taps and replay them verbatim.** Rejected outright: coordinate replay breaks on any layout, device-size, or translation change and violates determinism-by-selection. Resolving each tap to a stable `id` at capture time is the whole point.
* **Resolve a tapped point to the topmost / first matching element silently.** Rejected: that reintroduces "tap whatever matched first." When a point is ambiguous, capture must surface it for disambiguation, consistent with `resolve_unique`.
* **Fold capture into the existing AI `record` loop as an input mode.** Plausible but deferred to BE-0014, which defines the division of roles and the conversion between the two forms; this proposal scopes only the capture mechanism itself.

## References

[DESIGN §6.5](../../../DESIGN.md), `bajutsu/record.py`

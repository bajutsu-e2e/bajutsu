**English** · [日本語](BE-0002-m2-ai-loop-and-evidence-ja.md)

# BE-0002 — AI authoring loop & evidence (M2)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0002](BE-0002-m2-ai-loop-and-evidence.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0002") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Milestone 2 (M2) delivers the [Tier 1](../../docs/glossary.md#the-two-tiers) AI authoring loop (`record`) and the [evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) subsystem. The subsystem has three parts: `capturePolicy` rules, `video` and `deviceLog` interval captures, and the reporter (JUnit and HTML).

## Motivation

Authoring scenarios by hand is slow, and failures are hard to investigate without evidence. M2 lets AI write scenarios (Tier 1). M2 also normalizes "capture on every X" into reusable rules, so a deterministic re-run reproduces the same evidence without AI.

## Detailed design

M2 is built around three pieces:

- the `Agent` abstraction, with a Claude implementation and a system-alert guard;
- the evidence Sinks, which capture screenshots and elements instantly and capture `video` and `deviceLog` at intervals through simctl;
- the `capturePolicy` trigger rules.

Reports emit `manifest.json`, JUnit XML, and a self-contained HTML file.

## Alternatives considered

Idempotent normalization and provenance comments remain lightweight. They are a follow-up, not an alternative.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[recording.md](../../docs/recording.md), [evidence.md](../../docs/evidence.md), [reporting.md](../../docs/reporting.md), `bajutsu/record.py`

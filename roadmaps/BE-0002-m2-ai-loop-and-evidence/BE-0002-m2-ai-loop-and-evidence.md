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

The Tier 1 AI authoring loop (`record`) plus the evidence subsystem: `capturePolicy` rules, `video` / `deviceLog` interval captures, and the reporter (JUnit / HTML).

## Motivation

Authoring scenarios by hand is slow, and failures are hard to investigate without evidence. M2 lets AI write scenarios (Tier 1) and normalizes "capture on every X" into reusable rules so a deterministic re-run reproduces the same evidence without AI.

## Detailed design

Built around the `Agent` abstraction (a Claude implementation + a system-alert guard), the evidence Sinks (instant screenshot / elements; interval video / deviceLog via simctl), and the `capturePolicy` trigger rules; reports emit `manifest.json` + JUnit XML + a self-contained HTML.

## Alternatives considered

Idempotent normalization / provenance comments remain light — a follow-up rather than an alternative.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[recording.md](../../docs/recording.md), [evidence.md](../../docs/evidence.md), [reporting.md](../../docs/reporting.md), `bajutsu/record.py`

**English** · [日本語](BE-0004-m4-self-healing-triage-ja.md)

# BE-0004 — Self-healing triage (M4)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0004](BE-0004-m4-self-healing-triage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0004") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Milestones (M1–M4) |
<!-- /BE-METADATA -->

## Introduction

Self-healing triage: `bajutsu triage` assembles a failed run's context and diagnoses it (root cause + suggested fixes) — **advisory only, never the pass/fail judge**.

## Motivation

Regressions are expensive to maintain. M4 lowers that cost by letting AI investigate failures and propose minimal fixes, while keeping the determinism boundary intact: a fix is applied only when a human opts in after reviewing the diff.

## Detailed design

Diagnosis runs through one of two agents behind the same `TriageAgent` protocol: the rule-based `HeuristicTriageAgent` or `triage --ai` (Claude, which also reads the failure screenshot). An agent may propose a structured fix — `renameId`, `addIndex`, or `raiseTimeout` — which `--apply` shows as a dry-run diff, `--write` applies to the source, and `--rerun` re-verifies. Validated end-to-end on a real Simulator.

## Alternatives considered

Competitors auto-correct during a run; Bajutsu deliberately rejects implicit in-run correction to avoid "making tests laxer" ([DESIGN §11](../../DESIGN.md)).

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §3.1 / §12](../../DESIGN.md), [reporting.md](../../docs/reporting.md), `bajutsu/triage.py`, [Self-healing triage (M4)](../README.md#self-healing-triage-m4)

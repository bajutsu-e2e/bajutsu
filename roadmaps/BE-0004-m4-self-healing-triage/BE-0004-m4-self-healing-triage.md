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
| Topic | Self-healing triage |
<!-- /BE-METADATA -->

## Introduction

Milestone 4 (M4) delivers self-healing [triage](../../docs/glossary.md#evidence-capturepolicy-trace-triage). `bajutsu triage` assembles the context of a failed run and diagnoses it, returning a root cause and suggested fixes. The diagnosis is **advisory only and never the pass/fail judge**.

## Motivation

Regressions are expensive to maintain. M4 lowers that cost by letting AI investigate failures and propose minimal fixes. The determinism boundary stays intact: a fix is applied only when a human reviews the diff and opts in.

## Detailed design

Diagnosis runs through one of two agents behind the same `TriageAgent` protocol:

- the rule-based `HeuristicTriageAgent`;
- `triage --ai`, which uses Claude and also reads the failure screenshot.

An agent may propose a structured fix: `renameId`, `addIndex`, or `raiseTimeout`. Three flags then act on the fix. `--apply` shows it as a dry-run diff, `--write` applies it to the source, and `--rerun` re-verifies it. The flow was validated end to end on a real Simulator.

## Alternatives considered

Competing tools auto-correct during a run. Bajutsu deliberately rejects implicit in-run correction, to avoid "making tests laxer" ([DESIGN §11](../../DESIGN.md)).

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §3.1 / §12](../../DESIGN.md), [reporting.md](../../docs/reporting.md), `bajutsu/triage.py`, [Self-healing triage](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) (roadmap dashboard)

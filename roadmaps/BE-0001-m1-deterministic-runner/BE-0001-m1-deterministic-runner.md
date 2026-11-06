**English** · [日本語](BE-0001-m1-deterministic-runner-ja.md)

# BE-0001 — Deterministic runner (M1)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0001](BE-0001-m1-deterministic-runner.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0001") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

Milestone 1 (M1) delivers the deterministic [Tier 2](../../docs/glossary.md#the-two-tiers) runner. It wires the environment (simctl), the drivers, scenarios, assertions, lightweight evidence, the manifest, and per-app config behind `run` and `doctor`.

## Motivation

A test's pass/fail must be reproducible and free of AI. M1 establishes the deterministic foundation that every other component depends on. That foundation has three parts: stable selector resolution, condition-based waits (no fixed sleeps), and a clean environment for each run.

## Detailed design

The determinism core has five parts:

- the driver abstraction with selector resolution, which handles zero, one, and two-or-more matches;
- the simctl environment layer;
- the scenario schema, with strict validation;
- machine-checkable assertion evaluation;
- the manifest reporter.

An in-memory fake driver exercises the core. The idb backend validates it on a real Simulator: the `cross_backend.yaml` scenario passes on the device by preferring ids, and config alone switches the target app.

## Alternatives considered

[`DESIGN.md`](../../DESIGN.md) gives the rationale for putting determinism ahead of AI in the execution loop.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §2 / §3](../../DESIGN.md), [architecture.md](../../docs/architecture.md), `bajutsu/orchestrator.py`, `bajutsu/drivers/base.py`

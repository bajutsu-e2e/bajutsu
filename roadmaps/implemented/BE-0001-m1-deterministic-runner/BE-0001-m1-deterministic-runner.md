**English** · [日本語](BE-0001-m1-deterministic-runner-ja.md)

# BE-0001 — Deterministic runner (M1)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0001](BE-0001-m1-deterministic-runner.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Track | [Accepted](../../README.md#accepted) |
| Topic | Milestones (M1–M4) |
<!-- /BE-METADATA -->

## Introduction

The deterministic Tier 2 runner: environment (simctl) + drivers + scenarios + assertions + lightweight evidence + manifest + per-app config, wired behind `run` / `doctor`.

## Motivation

A test's pass/fail must be reproducible and AI-free. M1 establishes the deterministic foundation that all other components depend on: stable selector resolution, condition-based waits (no fixed sleeps), and a clean per-run environment.

## Detailed design

The determinism core is the driver abstraction + selector resolution (0/1/2+ match handling), the simctl environment layer, the scenario schema with strict validation, machine-checkable assertion evaluation, and the manifest reporter — exercised by an in-memory fake driver and validated on a real Simulator via the idb backend (the `cross_backend.yaml` scenario passes id-first on-device, target app switchable via config alone).

## Alternatives considered

See [`DESIGN.md`](../../../DESIGN.md) for the rationale behind determinism-first over AI-in-the-loop execution.

## References

[DESIGN §2 / §3](../../../DESIGN.md), [architecture.md](../../../docs/architecture.md), `bajutsu/orchestrator.py`, `bajutsu/drivers/base.py`

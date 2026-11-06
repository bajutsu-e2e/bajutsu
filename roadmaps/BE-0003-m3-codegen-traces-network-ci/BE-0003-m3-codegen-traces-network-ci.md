**English** · [日本語](BE-0003-m3-codegen-traces-network-ci-ja.md)

# BE-0003 — codegen, traces, network & CI (M3)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0003](BE-0003-m3-codegen-traces-network-ci.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0003") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

Milestone 3 (M3) delivers five capabilities:

- XCUITest codegen;
- app traces (`appTrace` and os_signpost);
- redaction of captured evidence;
- network **observation**, through an in-app collector and `request` assertions;
- **deterministic mocks**, which turn the scenario's `mocks` into offline in-protocol stubs.

M3 also adds continuous integration (CI).

## Motivation

A real pipeline adopts the tool only if it does four things: emit native tests, observe (and deterministically stub) the network, redact secrets in evidence, and gate every change in CI.

## Detailed design

codegen maps a scenario structurally to an equivalent XCUITest (Swift), with no AI at test time. Two workflows gate changes:

- `ci.yml` runs ruff, mypy, and pytest on Linux (py3.13).
- `e2e.yml` runs the idb smoke scenario and the codegen-to-XCUITest path (`make -C demos/features ui-test`) on a macOS Simulator.

Both were validated on a device.

## Alternatives considered

Declarative in-protocol `mocks` superseded the external `mockServer` command. The command remains unwired and is tracked as a deferred proposal.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[codegen.md](../../docs/codegen.md), [ci.md](../../docs/ci.md), `bajutsu/codegen.py`

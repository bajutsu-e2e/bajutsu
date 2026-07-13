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

XCUITest codegen, app traces (`appTrace` / os_signpost), redaction of captured evidence, network **observation** (in-app collector + `request` assertions), **deterministic mocks** (scenario `mocks` → offline in-protocol stubs), and CI.

## Motivation

To be adopted in real pipelines the tool must emit native tests, observe (and deterministically stub) network, redact secrets in evidence, and gate every change in CI (continuous integration).

## Detailed design

codegen maps a scenario to an equivalent XCUITest (Swift) structurally (no AI at test time). `ci.yml` runs ruff + mypy + pytest on Linux (py3.13); `e2e.yml` runs the idb smoke scenario and the codegen→XCUITest path (`make -C demos/features ui-test`) on a macOS Simulator. All validated on-device.

## Alternatives considered

The external `mockServer` command was superseded by declarative in-protocol `mocks` and remains unwired (tracked as a deferred proposal).

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[codegen.md](../../docs/codegen.md), [ci.md](../../docs/ci.md), `bajutsu/codegen.py`

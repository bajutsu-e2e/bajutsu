**English** · [日本語](BE-0003-m3-codegen-traces-network-ci-ja.md)

# BE-0003 — codegen, traces, network & CI (M3)

* Proposal: [BE-0003](BE-0003-m3-codegen-traces-network-ci.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Implemented**
* Implementing PR: predates the per-PR history (squashed into the initial import; no single PR)
* Track: [Accepted](../../README.md#accepted)
* Topic: Milestones (M1–M4)

## Introduction

XCUITest codegen, app traces (`appTrace` / os_signpost), redaction of captured evidence, network **observation** (in-app collector + `request` assertions), **deterministic mocks** (scenario `mocks` → offline in-protocol stubs), and CI.

## Motivation

To be adopted in real pipelines the tool must emit native tests, observe (and deterministically stub) network, redact secrets in evidence, and gate every change in CI (continuous integration).

## Detailed design

codegen maps a scenario to an equivalent XCUITest (Swift) structurally (no AI at test time). `ci.yml` runs ruff + mypy + pytest on Linux (py3.13); `e2e.yml` runs the idb smoke scenario and the codegen→XCUITest path (`make -C demos/features ui-test`) on a macOS Simulator. All validated on-device.

## Alternatives considered

The external `mockServer` command was superseded by declarative in-protocol `mocks` and remains unwired (tracked as a deferred proposal).

## References

[codegen.md](../../../docs/codegen.md), [ci.md](../../../docs/ci.md), `bajutsu/codegen.py`

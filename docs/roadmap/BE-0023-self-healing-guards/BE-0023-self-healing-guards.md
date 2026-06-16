**English** · [日本語](BE-0023-self-healing-guards-ja.md)

# BE-0023 — Guards against "making tests laxer"

* Proposal: [BE-0023](BE-0023-self-healing-guards.md)
* Status: **Implemented**
* Implementing PR: predates the per-PR history (squashed into the initial import; no single PR)
* Track: [Accepted](../README.md#accepted)
* Topic: Self-healing triage (M4)

## Introduction

This proposal addresses the risk that self-healing could weaken pass/fail criteria. A fix is **always reviewed by a human as a diff and explicitly applied with `--write`** (never auto-applied); a fragment mismatch is a safe no-op.

## Motivation

TBD.

## Detailed design

Implemented; see *References*.

## Alternatives considered

TBD.

## References

[DESIGN §11](../../../DESIGN.md)

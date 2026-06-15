**English** · [日本語](../ja/roadmap/BE-0023-self-healing-guards.md)

# BE-0023 — Guards against "making tests laxer"

* Proposal: [BE-0023](BE-0023-self-healing-guards.md)
* Status: **Implemented**
* Track: [Accepted](README.md#accepted)
* Topic: Self-healing triage (M4)

## Introduction

A brake against the risk of self-healing loosening pass/fail. A fix is **always reviewed by a human as a diff and explicitly applied with `--write`** (never auto-applied); a fragment mismatch is a safe no-op.

## Motivation

TBD.

## Detailed design

Implemented; see *References*.

## Alternatives considered

TBD.

## References

[DESIGN §11](../../DESIGN.md)

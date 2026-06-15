**English** · [日本語](BE-0030-parameterized-shared-steps-ja.md)

# BE-0030 — Parameterized shared steps

* Proposal: [BE-0030](BE-0030-parameterized-shared-steps.md)
* Status: **Implemented**
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

Define and call **reusable components with arguments** via the `use` step, expanding `${params.*}` (`expand_components`). Usable alongside the `setup` prelude (no args). Removes duplication in common steps like login.

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu/scenario.py` (`use`/`expand_components`).

## Alternatives considered

TBD.

## References

`bajutsu/scenario.py` (`use`/`expand_components`), [scenarios.md](../../scenarios.md)

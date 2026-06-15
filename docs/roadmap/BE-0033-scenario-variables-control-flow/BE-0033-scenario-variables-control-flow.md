**English** · [日本語](BE-0033-scenario-variables-control-flow-ja.md)

# BE-0033 — Scenario variables + light control flow

* Proposal: [BE-0033](BE-0033-scenario-variables-control-flow.md)
* Status: **Accepted, in progress**
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

The `${...}` interpolation primitive (`interp.py`, handling params/row/secrets uniformly) is implemented. What remains is capturing UI values for later reuse via `vars.*`, and conditionals and loops within bounds that preserve determinism.

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu/interp.py`.

## Alternatives considered

TBD.

## References

`bajutsu/interp.py`, [scenarios.md](../../scenarios.md)

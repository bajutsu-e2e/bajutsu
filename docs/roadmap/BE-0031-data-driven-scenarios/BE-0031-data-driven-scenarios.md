**English** · [日本語](BE-0031-data-driven-scenarios-ja.md)

# BE-0031 — Data-driven scenarios

* Proposal: [BE-0031](BE-0031-data-driven-scenarios.md)
* Status: **Implemented**
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

Repeat one scenario over multiple rows via `data` (inline) / `dataFile` (CSV). Substitute `${row.*}` per row (`expand_data`). Effective for multilingual / boundary-value testing.

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu/scenario.py` (`expand_data`).

## Alternatives considered

TBD.

## References

`bajutsu/scenario.py` (`expand_data`), [scenarios.md](../../scenarios.md)

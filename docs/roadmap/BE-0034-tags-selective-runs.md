**English** · [日本語](../ja/roadmap/BE-0034-tags-selective-runs.md)

# BE-0034 — Tags / labels + selective runs

* Proposal: [BE-0034](BE-0034-tags-selective-runs.md)
* Status: **Implemented**
* Track: [Accepted](README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

Run a subset of scenarios by `tags` with `--tag`/`--exclude` (include/exclude, exclude wins, `select_scenarios`). Effective for staged CI runs.

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu/scenario.py` (`select_scenarios`).

## Alternatives considered

TBD.

## References

`bajutsu/scenario.py` (`select_scenarios`), [cli.md](../cli.md)

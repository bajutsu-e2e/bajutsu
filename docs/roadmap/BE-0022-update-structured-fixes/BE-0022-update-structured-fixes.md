**English** · [日本語](BE-0022-update-structured-fixes-ja.md)

# BE-0022 — `update` (minimal-diff proposals = applying structured fixes)

* Proposal: [BE-0022](BE-0022-update-structured-fixes.md)
* Status: **Implemented**
* Track: [Accepted](../README.md#accepted)
* Topic: Self-healing triage (M4)

## Introduction

Update a broken scenario with a minimal diff instead of re-recording the whole thing. Triage proposes a structured fix (`renameId`/`addIndex`/`raiseTimeout`) → `--apply` (dry-run diff) / `--write` applies it to the source, `--rerun` verifies by re-running. The rename and addIndex closed loops are proven on a real device.

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu triage --apply`.

## Alternatives considered

TBD.

## References

[DESIGN §6.5](../../../DESIGN.md), `bajutsu triage --apply`

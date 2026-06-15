**English** · [日本語](../ja/roadmap/BE-0039-self-healing-propose-optin.md)

# BE-0039 — Self-healing limited to "propose + opt-in apply"

* Proposal: [BE-0039](BE-0039-self-healing-propose-optin.md)
* Status: **Implemented**
* Track: [Accepted](README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: Both

## Introduction

Both companies auto-correct during a run. Bajutsu stays with the self-healing triage's **propose a minimal diff → human reviews the diff → explicitly applies with `--write`** (no implicit in-run correction = the "making tests laxer" guard, [DESIGN §11](../../DESIGN.md)).

## Motivation

TBD.

## Detailed design

Implemented; see *References*.

## Alternatives considered

TBD.

## References

[Self-healing triage (M4)](README.md#self-healing-triage-m4)

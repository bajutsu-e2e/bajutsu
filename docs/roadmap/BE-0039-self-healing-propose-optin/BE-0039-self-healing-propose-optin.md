**English** · [日本語](BE-0039-self-healing-propose-optin-ja.md)

# BE-0039 — Self-healing limited to "propose + opt-in apply"

* Proposal: [BE-0039](BE-0039-self-healing-propose-optin.md)
* Status: **Implemented**
* Implementing PR: predates the per-PR history (squashed into the initial import; no single PR)
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: Both

## Introduction

Both companies auto-correct selectors during a run. Bajutsu stays with the self-healing triage approach: propose a minimal diff, have a human review it, and apply it explicitly with `--write`. There is no implicit in-run correction, which guards against silently relaxing test constraints (see "making tests laxer" in [DESIGN §11](../../../DESIGN.md)).

## Motivation

TBD.

## Detailed design

Implemented; see *References*.

## Alternatives considered

TBD.

## References

[Self-healing triage (M4)](../README.md#self-healing-triage-m4)

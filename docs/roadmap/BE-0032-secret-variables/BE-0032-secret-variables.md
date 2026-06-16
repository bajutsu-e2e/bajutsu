**English** · [日本語](BE-0032-secret-variables-ja.md)

# BE-0032 — Secret variables

* Proposal: [BE-0032](BE-0032-secret-variables.md)
* Status: **Implemented**
* Implementing PR: [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6)
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

Resolve `${secrets.X}` from environment variables for use in input, and automatically mask their real values in evidence (extending the existing `redact` down to input values). Declared in config under `secrets:`.

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu/interp.py` · `bajutsu/redaction.py`.

## Alternatives considered

TBD.

## References

`bajutsu/interp.py` · `bajutsu/redaction.py`, [evidence.md](../../evidence.md)

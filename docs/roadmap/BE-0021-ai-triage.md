**English** · [日本語](../ja/roadmap/BE-0021-ai-triage.md)

# BE-0021 — AI triage (root-cause summary, fix suggestions)

* Proposal: [BE-0021](BE-0021-ai-triage.md)
* Status: **Implemented**
* Track: [Accepted](README.md#accepted)
* Topic: Self-healing triage (M4)

## Introduction

AI reads the failure evidence and produces a root-cause summary and fix suggestions (human review assumed). `bajutsu triage` (rule-based) plus `--ai` (Claude, including the failure screenshot). The deterministic `trace` command is the layer beneath it.

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu/triage.py` · `bajutsu/claude_triage.py`.

## Alternatives considered

TBD.

## References

[DESIGN §3.1 / §12](../../DESIGN.md), `bajutsu/triage.py` · `bajutsu/claude_triage.py`

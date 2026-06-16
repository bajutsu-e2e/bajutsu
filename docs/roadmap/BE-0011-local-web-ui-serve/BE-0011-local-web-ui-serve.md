**English** · [日本語](BE-0011-local-web-ui-serve-ja.md)

# BE-0011 — Local web UI (`bajutsu serve`)

* Proposal: [BE-0011](BE-0011-local-web-ui-serve.md)
* Status: **Implemented**
* Implementing PR: predates the per-PR history (squashed into the initial import; no single PR)
* Track: [Accepted](../README.md#accepted)
* Topic: Authoring experience (record / GUI editor)

## Introduction

A small launcher that lists scenarios and apps, runs them with a single click, streams run logs, and displays the report in the browser (stdlib only). It is a Tier 1 convenience feature and is not part of the CI gate. It also serves as the foundation for a planned GUI editor (visual editing and element picker).

## Motivation

TBD.

## Detailed design

Implemented; see `bajutsu/serve.py`.

## Alternatives considered

TBD.

## References

`bajutsu/serve.py`, [cli.md](../../cli.md)

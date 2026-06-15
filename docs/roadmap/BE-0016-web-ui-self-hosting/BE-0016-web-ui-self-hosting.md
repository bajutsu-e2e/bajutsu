**English** · [日本語](BE-0016-web-ui-self-hosting-ja.md)

# BE-0016 — Self-hosting of the web UI

* Proposal: [BE-0016](BE-0016-web-ui-self-hosting.md)
* Status: **Proposal**
* Track: [Proposals](../README.md#proposals)
* Topic: Authoring experience (record / GUI editor)

## Introduction

A configuration for running the web UI on a personal Mac. Stage A uses Tailscale and a LaunchAgent to expose the current `serve` immediately. Stage B uses Docker Compose (Postgres / Redis / MinIO / Authelia) with a personal Mac worker pool. An operations guide will cover the requirement that the Simulator needs a GUI session.

## Motivation

TBD.

## Detailed design

TBD — to be specified when this proposal is taken up.

## Alternatives considered

TBD.

## References

[self-hosting.md](../../self-hosting.md), `bajutsu/serve.py`

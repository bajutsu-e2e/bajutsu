**English** · [日本語](BE-0015-web-ui-public-hosting-ja.md)

# BE-0015 — Public hosting of the web UI

* Proposal: [BE-0015](BE-0015-web-ui-public-hosting.md)
* Status: **Proposal**
* Track: [Proposals](../README.md#proposals)
* Topic: Authoring experience (record / GUI editor)

## Introduction

Convert the local `serve` into a shared, publicly accessible service. The architecture splits into a control plane (Linux: FastAPI + Postgres + Redis + R2) and a macOS worker pool (Orka), with auth, isolation, and per-run Simulators. This requires a core refactor that replaces `subprocess.Popen` with a job queue.

## Motivation

TBD.

## Detailed design

TBD — to be specified when this proposal is taken up.

## Alternatives considered

TBD.

## References

[cloud-hosting.md](../../cloud-hosting.md), `bajutsu/serve.py`

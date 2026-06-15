**English** · [日本語](../ja/roadmap/BE-0015-web-ui-public-hosting.md)

# BE-0015 — Public hosting of the web UI

* Proposal: [BE-0015](BE-0015-web-ui-public-hosting.md)
* Status: **Proposal**
* Track: [Proposals](README.md#proposals)
* Topic: Authoring experience (record / GUI editor)

## Introduction

Turn the local `serve` into a shared, public service. Split into a control plane (Linux: FastAPI + Postgres + Redis + R2) and a macOS worker pool (Orka), adding auth, isolation, and per-run Simulators. Entails a core refactor turning `subprocess.Popen` into a job queue.

## Motivation

TBD.

## Detailed design

TBD — to be specified when this proposal is taken up.

## Alternatives considered

TBD.

## References

[cloud-hosting.md](../cloud-hosting.md), `bajutsu/serve.py`

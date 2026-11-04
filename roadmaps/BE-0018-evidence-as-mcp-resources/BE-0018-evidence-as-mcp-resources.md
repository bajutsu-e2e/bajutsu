**English** · [日本語](BE-0018-evidence-as-mcp-resources-ja.md)

# BE-0018 — Return evidence as MCP resources

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0018](BE-0018-evidence-as-mcp-resources.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0018") |
| Implementing PR | [#68](https://github.com/bajutsu-e2e/bajutsu/pull/68) |
| Topic | Integration & automation |
<!-- /BE-METADATA -->

## Introduction

Run results such as `manifest.json` and `report.html` become resources that an agent reads through the Model Context Protocol (MCP).

## Motivation

The MCP server already exposes `bajutsu_run` and `bajutsu_doctor` as tools, so an agent can *start* a run. A run's value, however, lies in its output. `manifest.json` is the single source of truth for what each step did. `report.html` is the human-readable summary.

Today the tool's reply gives the agent only the manifest's filesystem path. The agent then reads that file out of band, which breaks the protocol boundary in three ways:

- The agent needs direct disk access.
- The agent must know the run layout (`runs/<runId>/manifest.json`).
- The host running the MCP server may not share a filesystem with the agent at all.

Exposing the [evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) as MCP resources closes that gap. A resource is the protocol's own way to hand a document to an agent. A URI addresses it, and the agent fetches it through the same channel as everything else. An agent that has just started a run can read the manifest back without leaving the protocol. That is what makes the "author with AI, investigate with AI" loop work over MCP, not only at a local shell.

## Detailed design

Run evidence is published as MCP resources under a `bajutsu://runs/...` URI scheme. The same `bajutsu mcp` server that exposes the tools also serves them:

- `bajutsu://runs/{run_id}/manifest.json` — the structured manifest for a named run.
- `bajutsu://runs/{run_id}/report.html` — the self-contained HTML report for that run.
- `bajutsu://runs/latest/manifest.json` — the manifest for the most recent run. An agent that has just called `bajutsu_run` reads the result without first parsing a run id out of the reply.

Each handler resolves the run id against the configured `runs/` directory and **rejects path traversal** before reading. A request resolves only to a file inside that directory, never above it. A missing run or a missing artifact returns a plain error, not an empty document. The agent can therefore tell "no such run" from "run produced no manifest".

The design stays inside the prime directives. Resources are **read-only**: they surface evidence the deterministic runner already wrote, and they add no path by which the LLM could influence pass or fail. The manifest an agent reads is the exact `manifest.json` that the run/CI gate judged against. The resource is a delivery channel, not a second source of truth. Resource serving is orthogonal to the run loop, so the gate stays entirely LLM-free.

The `runs/` directory is a server-level setting (the directory the server was pointed at), not a per-app one. Evidence from every app lands under the same run tree.

## Alternatives considered

**Return the manifest inline from `bajutsu_run`.** The tool could embed the whole manifest in its text reply. Rejected, for three reasons:

- A manifest with per-step evidence is large and grows with the run.
- Embedding it bloats every tool response, whether or not the agent needs the detail.
- Inline return offers no way to fetch an *earlier* run's evidence, only the run just executed.

Resources are addressable and fetched on demand.

**Leave the agent to read the file path.** This is the simplest option, because the tool already reports the manifest path. Rejected: it assumes a shared filesystem and forces the agent outside the protocol. The point of the MCP surface is that an agent need not know Bajutsu's on-disk layout.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[reporting.md](../../docs/reporting.md)

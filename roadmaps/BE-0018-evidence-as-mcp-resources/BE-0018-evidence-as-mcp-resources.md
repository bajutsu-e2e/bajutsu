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

Expose run results such as `manifest.json` / `report.html` as resources an agent can read.

## Motivation

The MCP (Model Context Protocol) server already exposes `bajutsu_run` and `bajutsu_doctor` as
tools, so an agent can *start* a run. But a run's value is in its output: `manifest.json` — the
single source of truth for what each step did — and `report.html`, the human-readable summary.
Today an agent only learns the manifest's filesystem path from the tool's reply, then has to read
that file out of band. That breaks the protocol boundary: the agent needs direct disk access, has
to know the run layout (`runs/<runId>/manifest.json`), and the host running the MCP server may not
share a filesystem with the agent at all.

Exposing the evidence as MCP resources closes that gap. A resource is the protocol's own way to
hand a document to an agent — addressable by URI, fetched through the same channel as everything
else. The agent that just kicked off a run can read its manifest back without leaving the
protocol, which is what makes the "author with AI, investigate with AI" loop work over MCP rather
than only at a local shell.

## Detailed design

Run evidence is published as MCP resources under a `bajutsu://runs/...` URI scheme, served by the
same `bajutsu mcp` server that exposes the tools:

- `bajutsu://runs/{run_id}/manifest.json` — the structured manifest for a named run.
- `bajutsu://runs/{run_id}/report.html` — the self-contained HTML report for that run.
- `bajutsu://runs/latest/manifest.json` — the manifest for the most recent run, so an agent that
  just called `bajutsu_run` can read the result without first parsing a run id out of the reply.

Each handler resolves the run id against the configured `runs/` directory and **rejects path
traversal** before reading — a request only ever resolves to a file inside that directory, never
above it. A missing run or missing artifact returns a plain error rather than an empty document,
so the agent can tell "no such run" from "run produced no manifest".

This stays inside the prime directives. Resources are **read-only**: they surface evidence the
deterministic runner already wrote, and add no path by which the LLM could influence pass/fail.
The manifest an agent reads is the exact same `manifest.json` the run/CI gate judged against —
the resource is a delivery channel, not a second source of truth. Because resource serving is
orthogonal to the run loop, the gate stays entirely LLM-free.

The `runs/` directory is a server-level setting (the directory the server was pointed at), not a
per-app one, since evidence from every app lands under the same run tree.

## Alternatives considered

**Return the manifest inline from `bajutsu_run`.** The tool could embed the whole manifest in its
text reply. Rejected: a manifest with per-step evidence is large and grows with the run, it would
bloat every tool response whether or not the agent needs the detail, and it gives no way to fetch
an *earlier* run's evidence — only the one just executed. Resources are addressable and fetched on
demand.

**Leave the agent to read the file path.** The simplest option — the tool already reports the
manifest path. Rejected because it assumes a shared filesystem and forces the agent outside the
protocol; the whole point of the MCP surface is that an agent need not know Bajutsu's on-disk
layout.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[reporting.md](../../docs/reporting.md)

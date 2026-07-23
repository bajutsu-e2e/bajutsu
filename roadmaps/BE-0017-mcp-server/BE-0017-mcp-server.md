**English** · [日本語](BE-0017-mcp-server-ja.md)

# BE-0017 — MCP server

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0017](BE-0017-mcp-server.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0017") |
| Implementing PR | [#44](https://github.com/bajutsu-e2e/bajutsu/pull/44) |
| Topic | Integration & automation |
<!-- /BE-METADATA -->

## Introduction

Expose Bajutsu commands as MCP (Model Context Protocol) tools so that AI agents (Claude Desktop / Code) can invoke them directly. This integrates with Tier 1 (AI authoring and investigation) — the deterministic gate stays unchanged.

## Motivation

Bajutsu already treats an AI agent as a first-class Tier-1 user: it authors and investigates scenarios. But an agent reaching Bajutsu through the shell has to construct CLI invocations, capture stdout, and parse free-form output to learn what happened. Exposing the commands as MCP tools gives the agent a typed surface it can call directly, and exposing each run's manifest and report as MCP resources lets it read structured results instead of scraping logs. The pass/fail judgement still comes only from the deterministic runner, so the gate is unchanged; MCP only makes the AI-facing paths (authoring, investigation) reachable without a shell.

## Detailed design

The MCP server lives in `bajutsu/mcp/` (optional dependency `fastmcp>=2.0.0`).

**Tools:**
- `bajutsu_doctor(app, udid)` — score the current screen's accessibility readiness (in-process)
- `bajutsu_run(app, scenario, ...)` — execute scenarios deterministically (subprocess)

**Resources:**
- `bajutsu://runs/{run_id}/manifest.json` — structured run result
- `bajutsu://runs/{run_id}/report.html` — self-contained HTML report
- `bajutsu://runs/latest/manifest.json` — most recent run

**Entry point:** `bajutsu mcp [--config ...] [--runs ...] [--transport stdio|sse]`

Install: `uv pip install bajutsu[mcp]`

## Alternatives considered

- In-process execution for `run` (rejected: device pool management is complex; subprocess is simpler and matches the `serve` pattern)
- HTTP transport as default (rejected: stdio is the standard for Claude Desktop / Code integration)

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/mcp/`, [cli.md](../../docs/cli.md)

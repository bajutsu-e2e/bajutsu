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

Bajutsu commands become Model Context Protocol (MCP) tools that AI agents such as Claude Desktop and Claude Code call directly. This serves [Tier 1](../../docs/glossary.md#the-two-tiers), where AI authors scenarios and investigates failures. The deterministic gate stays unchanged.

## Motivation

Bajutsu already treats an AI agent as a first-class Tier 1 user that authors and investigates scenarios. An agent that reaches Bajutsu through the shell must build CLI invocations, capture stdout, and parse free-form output to learn what happened.

MCP tools give the agent a typed surface to call directly. MCP resources expose each run's manifest and report, so the agent reads structured results instead of scraping logs. Only the deterministic runner judges pass or fail, so the gate is unchanged. MCP makes the AI-facing paths (authoring and investigation) reachable without a shell.

## Detailed design

The MCP server lives in `bajutsu/mcp/`. It needs the optional dependency `fastmcp>=2.0.0`.

**Tools:**
- `bajutsu_doctor(app, udid)` — scores the current screen's accessibility readiness (in-process)
- `bajutsu_run(app, scenario, ...)` — executes scenarios deterministically (subprocess)

**Resources:**
- `bajutsu://runs/{run_id}/manifest.json` — structured run result
- `bajutsu://runs/{run_id}/report.html` — self-contained HTML report
- `bajutsu://runs/latest/manifest.json` — most recent run

**Entry point:** `bajutsu mcp [--config ...] [--runs ...] [--transport stdio|sse]`

Install: `uv pip install bajutsu[mcp]`

## Alternatives considered

- **In-process execution for `run`.** Rejected: device pool management is complex. A subprocess is simpler and matches the `serve` pattern.
- **HTTP transport as the default.** Rejected: stdio is the standard transport for Claude Desktop and Claude Code integration.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/mcp/`, [cli.md](../../docs/cli.md)

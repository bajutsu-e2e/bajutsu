**English** · [日本語](BE-0017-mcp-server-ja.md)

# BE-0017 — MCP server

* Proposal: [BE-0017](BE-0017-mcp-server.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Implemented**
* Track: [Accepted](../../README.md#accepted)
* Topic: Integration & automation (MCP)

## Introduction

Expose Bajutsu commands as MCP (Model Context Protocol) tools so that AI agents (Claude Desktop / Code) can invoke them directly. This integrates with Tier 1 (AI authoring and investigation) — the deterministic gate stays unchanged.

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

## References

`bajutsu/mcp/`, [cli.md](../../../docs/cli.md)

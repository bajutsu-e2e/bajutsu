**English** · [日本語](BE-0018-evidence-as-mcp-resources-ja.md)

# BE-0018 — Return evidence as MCP resources

* Proposal: [BE-0018](BE-0018-evidence-as-mcp-resources.md)
* Status: **Implemented**
* Track: [Accepted](../README.md#accepted)
* Topic: Integration & automation (MCP)

## Introduction

Expose run evidence as MCP resources so AI agents can read results, screenshots, element trees, and network logs without filesystem access.

## Detailed design

Resources are registered in `bajutsu/mcp/resources.py` and served via the MCP server (BE-0017).

| Resource URI | Content |
|---|---|
| `bajutsu://runs/{run_id}/manifest.json` | Structured run result (JSON) |
| `bajutsu://runs/{run_id}/report.html` | Self-contained HTML report |
| `bajutsu://runs/{run_id}/junit.xml` | JUnit XML for CI integration |
| `bajutsu://runs/{run_id}/artifact/{path*}` | Any nested artifact (screenshots, elements.json, network.json, video, device logs) |
| `bajutsu://runs/latest/manifest.json` | Most recent run's manifest |

Text files (JSON/XML/HTML/YAML/log) are returned as strings; binary files (PNG, MP4) as bytes. All paths are validated against traversal (both escape from `runs/` and cross-run reads).

## Alternatives considered

- Exposing each artifact type as a separate named resource (rejected: the wildcard `{path*}` pattern is simpler and covers all current and future artifact types)

## References

`bajutsu/mcp/resources.py`, [reporting.md](../../reporting.md), [evidence.md](../../evidence.md)

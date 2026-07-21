**English** · [日本語](BE-XXXX-mcp-real-wire-protocol-test-ja.md)

# BE-XXXX — Real wire-protocol round-trip test for the MCP server

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-mcp-real-wire-protocol-test.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Integration & automation (MCP) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu mcp` exposes `bajutsu_run`/`bajutsu_doctor` as MCP tools and run evidence as resources, for
Claude Desktop/Code integration. Every test in `tests/test_mcp.py` calls `mcp.call_tool(...)` /
`mcp.read_resource(...)` directly on an in-process `FastMCP` instance; `test_cli_mcp_starts_server`
monkeypatches `create_server` so `.run()` never starts a real server, only records which transport
string was requested. Nothing in the suite ever serializes a tool call over stdio or SSE and
deserializes it back. This item adds a real wire-protocol round-trip test.

## Motivation

Calling `mcp.call_tool(...)` in-process exercises FastMCP's Python-level dispatch — the tool
function actually runs, and its return value is exactly what the test sees. It does not exercise the
JSON-RPC framing, tool-schema advertisement, or resource-URI encoding that only happen when a real
client and server talk over an actual transport. A schema Claude Desktop/Code cannot parse, a
resource URI that doesn't round-trip through real serialization, or a stdio framing bug would all
pass every test in the current suite, because the suite never puts anything on the wire.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Spin up a real server process.** Start `bajutsu mcp` as an actual subprocess (or in-process
  server bound to a real transport) rather than monkeypatching `create_server`.
- **Connect with a real MCP client.** Use the `mcp` SDK's client to list tools, call
  `bajutsu_run`/`bajutsu_doctor`, and read a resource over the real transport (stdio to start, since
  it needs no network), asserting the round-trip produces the same result the in-process tests
  already assert for the tool logic itself.
- **Keep the in-process tests as they are.** They remain the right tool for testing the tool
  functions' own logic; this item adds the wire-level layer underneath them rather than replacing
  them.
- **Land as a required check.** A local stdio round-trip needs no external service and carries no
  flakiness risk beyond the existing suite's — there is no reason to stage this as non-gating first.

## Alternatives considered

- **Trust the in-process tests, since FastMCP is a well-tested library.** FastMCP's own tests cover
  FastMCP; they say nothing about whether Bajutsu's specific tool schemas and resource URIs actually
  survive real serialization, which is what a real client round-trip is for.
- **Manually verify the MCP integration once against Claude Desktop and call it done.** A one-time
  manual check catches today's state but not a future regression; a CI-run round-trip test is the
  only form that keeps observing it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Start a real `bajutsu mcp` server process (or in-process server on a real transport).
- [ ] Connect with the real `mcp` SDK client and round-trip a tool call and a resource read.
- [ ] Wire it into CI as a required check.

## References

- `bajutsu/mcp/tools.py`, `bajutsu/mcp/resources.py`, `tests/test_mcp.py`
  (`test_cli_mcp_starts_server`)

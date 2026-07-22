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
| Related | [BE-0017](../BE-0017-mcp-server/BE-0017-mcp-server.md), [BE-0018](../BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu mcp` exposes `bajutsu_run`/`bajutsu_doctor` as MCP tools and run evidence as resources, for
Claude Desktop/Code integration. Most tests in `tests/test_mcp.py` call `mcp.call_tool(...)` /
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
  server bound to a real transport) rather than monkeypatching `create_server`. The client waits on
  a condition — the server's readiness signal, or its first request actually completing — rather
  than a fixed `sleep`, before it starts talking to the process (prime directive 2).
- **Connect with a real MCP client.** Use the `mcp` SDK's client to list tools, call
  `bajutsu_run`/`bajutsu_doctor`, and read a resource over the real transport (stdio to start, since
  it needs no network), asserting the round-trip produces the same result the in-process tests
  already assert for the tool logic itself.
- **Keep the in-process tests as they are.** They remain the right tool for testing the tool
  functions' own logic; this item adds the wire-level layer underneath them rather than replacing
  them.
- **Non-gating first.** Spawning a real subprocess and doing IPC over stdio carries more
  timing-sensitive surface than the current in-process calls — process-spawn latency and pipe
  buffering under CI resource contention — even with a readiness condition wait instead of a fixed
  `sleep`. Land it as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md),
  and promote it once stable.

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

- [ ] Start a real `bajutsu mcp` server process (or in-process server on a real transport), gated on
  a readiness condition wait rather than a fixed `sleep`.
- [ ] Connect with the real `mcp` SDK client and round-trip a tool call and a resource read.
- [ ] Keep the in-process tests as they are.
- [ ] Wire it into CI as a non-gating signal, promote to required once stable.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/mcp/tools.py`, `bajutsu/mcp/resources.py`, `tests/test_mcp.py`
  (`test_cli_mcp_starts_server`)

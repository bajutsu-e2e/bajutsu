**English** · [日本語](BE-0301-mcp-real-wire-protocol-test-ja.md)

# BE-0301 — Real wire-protocol round-trip test for the MCP server

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0301](BE-0301-mcp-real-wire-protocol-test.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0301") |
| Implementing PR | [#1345](https://github.com/bajutsu-e2e/bajutsu/pull/1345) |
| Topic | Integration & automation |
| Related | [BE-0017](../BE-0017-mcp-server/BE-0017-mcp-server.md), [BE-0018](../BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu mcp` exposes `bajutsu_run` and `bajutsu_doctor` as Model Context Protocol (MCP) tools, and
run evidence as resources, for Claude Desktop and Claude Code integration.

Most tests in `tests/test_mcp.py` call `mcp.call_tool(...)` and `mcp.read_resource(...)` directly on an
in-process `FastMCP` instance. `test_cli_mcp_starts_server` monkeypatches `create_server`, so `.run()`
never starts a real server and merely records which transport string was requested. Nothing in the
suite serializes a tool call over stdio or Server-Sent Events (SSE) and deserializes it back. This item
adds a real wire-protocol round-trip test.

## Motivation

Calling `mcp.call_tool(...)` in-process exercises FastMCP's Python-level dispatch. The tool function
runs, and its return value is exactly what the test sees. The call does not exercise the JSON-RPC
framing, the tool-schema advertisement, or the resource-URI encoding. Those happen only when a real
client and server talk over an actual transport.

Three defects would pass every test in the current suite, because the suite never puts anything on the
wire:

- A schema that Claude Desktop or Claude Code cannot parse.
- A resource URI that does not round-trip through real serialization.
- A stdio framing bug.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Spin up a real server process.** Start `bajutsu mcp` as an actual subprocess (or as an in-process
  server bound to a real transport) instead of monkeypatching `create_server`. The client waits on a
  condition before it talks to the process: the server's readiness signal, or its first request
  completing. It never waits on a fixed `sleep` (prime directive 2).
- **Connect with a real MCP client.** Use the `mcp` SDK's client over a real transport. Start with
  stdio, which needs no network. The client lists tools, calls `bajutsu_run` and `bajutsu_doctor`, and
  reads a resource. The test asserts that the round-trip produces the same result the in-process tests
  already assert for the tool logic itself.
- **Keep the in-process tests as they are.** They remain the right tool for testing the tool functions'
  own logic. This item adds the wire-level layer underneath them and does not replace them.
- **Non-gating first.** Spawning a real subprocess and doing IPC over stdio carries more
  timing-sensitive surface than the current in-process calls. Process-spawn latency and pipe buffering
  under CI resource contention both matter, even with a readiness condition wait instead of a fixed
  `sleep`. Land the test as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), and
  promote it once it is stable.

## Alternatives considered

- **Trust the in-process tests, since FastMCP is a well-tested library.** FastMCP's own tests cover
  FastMCP. They say nothing about whether Bajutsu's specific tool schemas and resource URIs survive real
  serialization, and a real client round-trip exists to answer exactly that.
- **Manually verify the MCP integration once against Claude Desktop and call it done.** A one-time
  manual check catches today's state and misses a future regression. Only a CI-run round-trip test
  keeps observing the integration.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Start a real `bajutsu mcp` server process (or in-process server on a real transport), gated on
  a readiness condition wait rather than a fixed `sleep`.
- [x] Connect with the real `mcp` SDK client and round-trip a tool call and a resource read.
- [x] Keep the in-process tests as they are.
- [x] Wire it into CI as a non-gating signal, promote to required once stable.

**Log**

- `tests/test_mcp_wire.py` starts `bajutsu mcp --transport stdio` as a real subprocess and drives it
  with the `mcp` SDK's `fastmcp.Client` over stdio. The client's `initialize` handshake is the
  readiness condition, so there is no fixed `sleep`.
- Over the wire, the suite covers the following.
  - It lists tools, asserting that `bajutsu_doctor` and `bajutsu_run` are both advertised with their
    input schemas.
  - It round-trips a `bajutsu_doctor` call and a `bajutsu_run` call.
  - It reads a `bajutsu://runs/{run_id}/manifest.json` resource.
  - It round-trips the wildcard-template resource (`artifact/{path*}`), the URI most exposed to
    encoding quirks.
  - It asserts that the non-string tool params serialize with their JSON-schema types (`erase` as
    boolean, `workers` as integer).
  - It covers the error path: a missing resource, and a tool call missing its required argument. Each
    is asserted to come back as a JSON-RPC error frame carrying the server's message. That
    serialization path is distinct from a successful call.
- The subprocess uses the `fake` backend, so `bajutsu_doctor` runs a real, device-free driver query in
  the server process. `bajutsu_run` spawns a real `bajutsu run` subprocess, whose verdict depends on
  the target environment.
  - The fake actuator itself needs no device. But the run reaches it only after its
    udid/device-resolution step.
  - `fake` is one of the simctl family (`fake` and `xcuitest`). That family's environment resolves the
    udid through `simctl.resolve_udid`, which shells out to `xcrun` for the default `booted` udid.
  - On the Linux host, where `xcrun` is absent, the resolution raises. `playwright` short-circuits
    before udid resolution, and `adb` resolves via `adb.resolve_serial`, not `xcrun`.
  - The run round-trip therefore asserts that the verdict *line* survives the transport in well-formed
    shape. That is the wire property under test. It does not assert the run's own pass/fail.
- The in-process `tests/test_mcp.py` is unchanged. The scope is the `stdio` transport. The `sse`
  transport's distinct framing is a deliberate follow-up.
- The suite is marked `mcp_wire`, and pyproject `addopts` deselects it from the fast gate. It runs in
  the non-gating `mcp-wire.yml` lane, to be promoted to required once stable, which is the precedent
  BE-0282 set.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/mcp/tools.py`, `bajutsu/mcp/resources.py`, `tests/test_mcp.py`
  (`test_cli_mcp_starts_server`)

"""Real wire-protocol round-trip test for the MCP server (BE-0301).

Everything in ``test_mcp.py`` calls the tools/resources in-process on a ``FastMCP`` instance, so
it exercises FastMCP's Python-level dispatch but never the JSON-RPC framing, tool-schema
advertisement, or resource-URI encoding that only happen over a real transport. This module closes
that gap: it starts ``bajutsu mcp`` as an actual subprocess and drives it with the real ``mcp`` SDK
client over stdio, so a schema Claude Desktop/Code could not parse, a resource URI that does not
survive real serialization, or an error that does not round-trip as a proper JSON-RPC error frame
fails here rather than passing silently.

The subprocess uses the ``fake`` backend, so ``bajutsu_doctor`` runs a real driver query in the
server process — device-free, so its computed result round-trips on any host. ``bajutsu_run``
spawns a real ``bajutsu run`` subprocess whose verdict depends on the target environment (its
device resolution needs the platform CLIs, absent on the Linux CI host); the test therefore asserts
that its verdict *line* round-trips in well-formed shape, which is the wire property under test, not
that the run itself passes.

Scope is the ``stdio`` transport (it needs no network); the ``sse`` transport's distinct framing is
a deliberate follow-up (see the item's Progress log). Marked ``mcp_wire`` so it stays out of the
fast gate (a real subprocess plus stdio IPC is more timing-sensitive than an in-process call); it
runs as a non-gating CI signal (``mcp-wire.yml``), promoted to required once stable — the precedent
BE-0282 set.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

pytestmark = pytest.mark.mcp_wire


def _roundtrip(config: Path, runs: Path, scenario: Path) -> dict[str, Any]:
    """Drive a real ``bajutsu mcp`` subprocess over stdio and collect one round-trip of each surface.

    Opening the client runs the MCP ``initialize`` handshake, which is itself the readiness
    condition — the client blocks until the server answers, so no fixed sleep is needed before
    talking to the process (prime directive 2).

    Both a happy path (tool schemas, a doctor/run call, a top-level and a wildcard-template resource
    read) and two error paths (a missing resource, a tool call missing a required argument) are
    driven in one session, since the interactions are independent and share no mutable state.
    """

    async def _capture_error(coro: Any) -> str:
        """Await an interaction expected to fail and return the error text that crossed the wire.

        An error becomes a JSON-RPC error frame rather than a Python exception in-process, so the
        SDK re-raises it client-side; the message it carries is what proves the frame round-tripped.
        """
        try:
            await coro
        except Exception as exc:  # the SDK's transport exception (McpError / ToolError)
            return str(exc)
        raise AssertionError("expected the interaction to fail over the wire, but it succeeded")

    async def _drive() -> dict[str, Any]:
        transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bajutsu",
                "mcp",
                "--transport",
                "stdio",
                "--config",
                str(config),
                "--runs",
                str(runs),
            ],
            cwd=str(config.parent),
        )
        async with Client(transport) as client:
            tools = await client.list_tools()
            doctor = await client.call_tool("bajutsu_doctor", {"target": "demo"})
            run = await client.call_tool(
                "bajutsu_run", {"target": "demo", "scenario": str(scenario)}
            )
            manifest = await client.read_resource("bajutsu://runs/run1/manifest.json")
            artifact = await client.read_resource(
                "bajutsu://runs/run1/artifact/00-scenario/step0/elements.json"
            )
            missing_resource_error = await _capture_error(
                client.read_resource("bajutsu://runs/nonexistent/manifest.json")
            )
            # `bajutsu_doctor` requires `target`; omitting it is a schema-validation failure the
            # server reports as a JSON-RPC error, distinct from the resource error above.
            bad_argument_error = await _capture_error(client.call_tool("bajutsu_doctor", {}))
        return {
            "tools": {t.name: t for t in tools},
            "doctor_text": doctor.content[0].text,
            "run_text": run.content[0].text,
            "manifest_text": manifest[0].text,
            "artifact_text": artifact[0].text,
            "missing_resource_error": missing_resource_error,
            "bad_argument_error": bad_argument_error,
        }

    return asyncio.run(_drive())


@pytest.fixture(scope="module")
def wire(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Spawn the server once and round-trip every surface, so each test asserts on a shared result.

    Starting a subprocess per test would multiply the timing-sensitive surface for no gain: the
    interactions are independent reads/calls, so one session covers them all.
    """
    root = tmp_path_factory.mktemp("mcp_wire")
    config = root / "bajutsu.config.yaml"
    config.write_text(
        "defaults: {}\ntargets:\n  demo:\n    bundleId: com.demo\n    backend: [fake]\n",
        encoding="utf-8",
    )
    runs = root / "runs"
    (runs / "run1").mkdir(parents=True)
    (runs / "run1" / "manifest.json").write_text(
        json.dumps({"runId": "run1", "ok": True}), encoding="utf-8"
    )
    artifact = runs / "run1" / "00-scenario" / "step0"
    artifact.mkdir(parents=True)
    (artifact / "elements.json").write_text(json.dumps([{"identifier": "ok"}]), encoding="utf-8")
    scenario = root / "noop.yaml"
    scenario.write_text("- name: noop\n  steps: []\n", encoding="utf-8")
    return _roundtrip(config, runs, scenario)


def test_wire_advertises_both_tools_with_typed_schemas(wire: dict[str, Any]) -> None:
    # The tool list and each tool's input schema are advertised over JSON-RPC, not read from the
    # in-process registry — the serialized schema a real client parses.
    assert set(wire["tools"]) == {"bajutsu_doctor", "bajutsu_run"}
    for name in ("bajutsu_doctor", "bajutsu_run"):
        assert "target" in wire["tools"][name].inputSchema["properties"]
    # The non-string params are where "a schema Claude Desktop/Code could not parse" would surface:
    # their JSON-schema types must serialize from the Python annotations, not collapse to strings.
    run_properties = wire["tools"]["bajutsu_run"].inputSchema["properties"]
    assert run_properties["erase"]["type"] == "boolean"
    assert run_properties["workers"]["type"] == "integer"


def test_wire_round_trips_the_doctor_tool(wire: dict[str, Any]) -> None:
    # The fake backend's empty screen yields a deterministic doctor render; asserting its structure
    # survived proves the multi-line result serialized back intact.
    text = wire["doctor_text"]
    assert "grade:" in text
    assert "idCoverage" in text


def test_wire_round_trips_the_run_tool(wire: dict[str, Any]) -> None:
    # `bajutsu_run` spawns a real `bajutsu run` subprocess and returns its verdict; the wire property
    # under test is that the verdict line survives the transport, not the run's own pass/fail (which
    # needs the platform CLIs — present on macOS, absent on the Linux CI host). The first token is
    # always PASS or FAIL, and the deterministic two-space `PASS|FAIL  <manifest>` shape means a PASS
    # carries a manifest path — so the verdict line's structure round-tripped intact either way.
    verdict_line = wire["run_text"].splitlines()[0]
    token = verdict_line.split()[0]
    assert token in {"PASS", "FAIL"}
    if token == "PASS":
        assert "manifest.json" in verdict_line


def test_wire_round_trips_a_top_level_resource(wire: dict[str, Any]) -> None:
    # The resource URI is encoded by the client and decoded by the server, and the file contents
    # come back over the transport — the URI-encoding path the in-process tests never touch.
    assert json.loads(wire["manifest_text"]) == {"runId": "run1", "ok": True}


def test_wire_round_trips_a_wildcard_template_resource(wire: dict[str, Any]) -> None:
    # `bajutsu://runs/{run_id}/artifact/{path*}` is the one templated URI with a wildcard segment —
    # the resource most exposed to encoding quirks, so round-tripping a nested path proves the
    # multi-segment template resolves over the wire, not just the flat top-level URIs.
    assert json.loads(wire["artifact_text"]) == [{"identifier": "ok"}]


def test_wire_propagates_a_resource_error(wire: dict[str, Any]) -> None:
    # A server-side `ValueError` becomes a JSON-RPC error frame, an entirely different serialization
    # path from a successful read; the server's message must survive it (in-process, test_mcp.py
    # asserts the same "no manifest" text on the raised exception).
    assert "no manifest" in wire["missing_resource_error"]


def test_wire_propagates_a_tool_argument_error(wire: dict[str, Any]) -> None:
    # Omitting the required `target` is rejected by schema validation and reported as a JSON-RPC
    # error — the bad-argument path a real client hits, distinct from the resource error above.
    assert "validation" in wire["bad_argument_error"].lower()

"""`bajutsu mcp` — start the MCP server for AI agent integration (Model Context Protocol)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bajutsu.cli._shared import DEFAULT_CONFIG, WORKER_CONFIG_HELP, _load_device_worker_or_exit
from bajutsu.common.run_meta.files import DEFAULT_RUNS_DIR

_MCP_TRANSPORTS = ("stdio", "sse")


def mcp(
    config: str = typer.Option(DEFAULT_CONFIG, "--config", help="Config file path"),
    runs: str = typer.Option(DEFAULT_RUNS_DIR, "--runs", help="Runs output directory"),
    transport: str = typer.Option(
        "stdio", "--transport", help="MCP transport: stdio (Claude Desktop/Code) or sse"
    ),
    worker_config: Annotated[
        Path | None, typer.Option("--worker-config", help=WORKER_CONFIG_HELP)
    ] = None,
) -> None:
    """Start the MCP server for AI agent integration (Model Context Protocol)."""
    if transport not in _MCP_TRANSPORTS:
        typer.echo(f"unsupported transport {transport!r} (choose from {_MCP_TRANSPORTS})")
        raise typer.Exit(2)
    # Read once at startup, so a bad file fails here rather than on the first tool call; the tools
    # then drive the grid an `appium` environment names (BE-0452).
    worker = _load_device_worker_or_exit(worker_config)
    try:
        from bajutsu.mcp import create_server
    except ImportError:
        typer.echo("fastmcp is not installed — run: uv pip install 'bajutsu[mcp]'")
        raise typer.Exit(2) from None
    server = create_server(Path(config), Path(runs), worker_config=worker_config, worker=worker)
    server.run(transport=transport)  # type: ignore[arg-type]


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(mcp)

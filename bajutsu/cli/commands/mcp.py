"""`bajutsu mcp` — start the MCP server for AI agent integration (Model Context Protocol)."""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu.cli._shared import DEFAULT_CONFIG

_MCP_TRANSPORTS = ("stdio", "sse")


def mcp(
    config: str = typer.Option(DEFAULT_CONFIG, "--config", help="Config file path"),
    runs: str = typer.Option("runs", "--runs", help="Runs output directory"),
    transport: str = typer.Option(
        "stdio", "--transport", help="MCP transport: stdio (Claude Desktop/Code) or sse"
    ),
) -> None:
    """Start the MCP server for AI agent integration (Model Context Protocol)."""
    if transport not in _MCP_TRANSPORTS:
        typer.echo(f"unsupported transport {transport!r} (choose from {_MCP_TRANSPORTS})")
        raise typer.Exit(2)
    try:
        from bajutsu.mcp import create_server
    except ImportError:
        typer.echo("fastmcp is not installed — run: uv pip install 'bajutsu[mcp]'")
        raise typer.Exit(2) from None
    server = create_server(Path(config), Path(runs))
    server.run(transport=transport)  # type: ignore[arg-type]


def register(app: typer.Typer) -> None:
    app.command()(mcp)

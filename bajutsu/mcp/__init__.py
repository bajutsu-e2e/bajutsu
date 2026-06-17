"""MCP (Model Context Protocol) server for Bajutsu.

Exposes ``run`` and ``doctor`` as MCP tools, and run evidence as MCP resources,
so AI agents (Claude Desktop / Code) can drive Bajutsu directly. All tools stay
on the Tier-1 side of the boundary: agents author and investigate, the
deterministic gate stays unchanged.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from bajutsu.mcp.resources import register_resources
from bajutsu.mcp.tools import register_tools


def create_server(config_path: Path, runs_dir: Path = Path("runs")) -> FastMCP:
    """Build a configured MCP server with all tools and resources registered.

    ``runs_dir`` controls where the resource endpoints read evidence from.
    The ``bajutsu_run`` tool writes to the CLI's default ``runs/`` (relative
    to cwd), so ``runs_dir`` should match that location."""
    mcp = FastMCP("bajutsu")
    register_tools(mcp, config_path)
    register_resources(mcp, runs_dir)
    return mcp

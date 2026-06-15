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
    """Build a configured MCP server with all tools and resources registered."""
    mcp = FastMCP("bajutsu")
    register_tools(mcp, config_path, runs_dir)
    register_resources(mcp, runs_dir)
    return mcp

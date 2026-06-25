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

    Args:
        config_path: Path to the Bajutsu config file (passed through to all tools).
        runs_dir: Root directory for run evidence. Resource endpoints read from here;
            should match the CLI's default ``runs/`` (relative to cwd) so the
            ``bajutsu_run`` tool and the resource URIs resolve to the same location.

    Returns:
        A ``FastMCP`` instance with ``bajutsu_doctor``, ``bajutsu_run``, and all
        run-evidence resources already registered.
    """
    mcp = FastMCP("bajutsu")
    register_tools(mcp, config_path)
    register_resources(mcp, runs_dir)
    return mcp

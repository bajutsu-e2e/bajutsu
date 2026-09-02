"""MCP (Model Context Protocol) server for Bajutsu.

Exposes ``run`` and ``doctor`` as MCP tools, and run evidence as MCP resources,
so AI agents (Claude Desktop / Code) can drive Bajutsu directly. All tools stay
on the Tier-1 side of the boundary: agents author and investigate, the
deterministic gate stays unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.run_files import DEFAULT_RUNS_DIR

if TYPE_CHECKING:
    from fastmcp import FastMCP


def create_server(config_path: Path, runs_dir: Path = Path(DEFAULT_RUNS_DIR)) -> FastMCP:
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
    # Deferred: `bajutsu.mcp` is now a package `bajutsu.mcp.cli` lives inside, so merely
    # importing the CLI command (done unconditionally for every feature, to build the Typer
    # app) would otherwise import this module and force the optional `fastmcp` dependency —
    # breaking the same "extras stay off the default import path" guarantee `ai`/`web` rely on.
    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources
    from bajutsu.mcp.tools import register_tools

    mcp = FastMCP("bajutsu")
    register_tools(mcp, config_path)
    register_resources(mcp, runs_dir)
    return mcp

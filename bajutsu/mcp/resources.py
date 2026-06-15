"""MCP resource definitions: run evidence (manifest, report)."""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from bajutsu.trace import latest_run


def register_resources(mcp: FastMCP, runs_dir: Path) -> None:

    @mcp.resource("bajutsu://runs/{run_id}/manifest.json")
    def run_manifest(run_id: str) -> str:
        """The manifest.json for a completed run (structured JSON)."""
        path = runs_dir / run_id / "manifest.json"
        if not path.is_file():
            raise ValueError(f"no manifest for run {run_id}")
        return path.read_text(encoding="utf-8")

    @mcp.resource("bajutsu://runs/{run_id}/report.html")
    def run_report(run_id: str) -> str:
        """The self-contained HTML report for a completed run."""
        path = runs_dir / run_id / "report.html"
        if not path.is_file():
            raise ValueError(f"no report for run {run_id}")
        return path.read_text(encoding="utf-8")

    @mcp.resource("bajutsu://runs/latest/manifest.json")
    def latest_manifest() -> str:
        """The manifest.json for the most recent run."""
        path = latest_run(runs_dir)
        if path is None:
            raise ValueError("no runs found")
        manifest = path / "manifest.json"
        if not manifest.is_file():
            raise ValueError(f"no manifest in latest run {path.name}")
        return manifest.read_text(encoding="utf-8")

"""MCP resource definitions: run evidence (manifest, report)."""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from bajutsu.trace import latest_run


def _safe_run_path(runs_dir: Path, run_id: str, filename: str) -> Path:
    """Resolve a run artifact path, rejecting path traversal."""
    target = (runs_dir / run_id / filename).resolve()
    base = runs_dir.resolve()
    if base not in target.parents:
        raise ValueError(f"invalid run_id: {run_id}")
    return target


def _safe_artifact_path(runs_dir: Path, run_id: str, rel_path: str) -> Path:
    """Resolve a nested artifact path, rejecting path traversal.

    Ensures the resolved path stays under ``runs_dir/run_id``, preventing
    both escape from runs_dir and cross-run reads.
    """
    run_base = (runs_dir / run_id).resolve()
    target = (run_base / rel_path).resolve()
    if run_base not in target.parents and target != run_base:
        raise ValueError(f"invalid artifact path: {rel_path}")
    return target


def _read_text_or_binary(path: Path) -> str | bytes:
    """Read a file as text (JSON/XML/YAML/HTML) or binary (images, video)."""
    text_suffixes = {".json", ".xml", ".yaml", ".yml", ".html", ".log", ".txt"}
    if path.suffix in text_suffixes:
        return path.read_text(encoding="utf-8")
    return path.read_bytes()


def register_resources(mcp: FastMCP, runs_dir: Path) -> None:
    """Register run-evidence resources on *mcp* rooted at *runs_dir*.

    All resource handlers reject path traversal so callers cannot escape
    ``runs_dir``. Text formats (JSON/XML/HTML/YAML) are returned as strings;
    binary formats (images, video) as bytes.
    """

    @mcp.resource("bajutsu://runs/{run_id}/manifest.json")
    def run_manifest(run_id: str) -> str:
        """The manifest.json for a completed run (structured JSON)."""
        path = _safe_run_path(runs_dir, run_id, "manifest.json")
        if not path.is_file():
            raise ValueError(f"no manifest for run {run_id}")
        return path.read_text(encoding="utf-8")

    @mcp.resource("bajutsu://runs/{run_id}/report.html")
    def run_report(run_id: str) -> str:
        """The self-contained HTML report for a completed run."""
        path = _safe_run_path(runs_dir, run_id, "report.html")
        if not path.is_file():
            raise ValueError(f"no report for run {run_id}")
        return path.read_text(encoding="utf-8")

    @mcp.resource("bajutsu://runs/{run_id}/junit.xml")
    def run_junit(run_id: str) -> str:
        """The JUnit XML for a completed run (CI integration)."""
        path = _safe_run_path(runs_dir, run_id, "junit.xml")
        if not path.is_file():
            raise ValueError(f"no junit.xml for run {run_id}")
        return path.read_text(encoding="utf-8")

    @mcp.resource("bajutsu://runs/{run_id}/artifact/{path*}")
    def run_artifact(run_id: str, path: str) -> str | bytes:
        """Any artifact under a run directory (screenshots, elements.json, network.json, video, …).

        Text files are returned as strings; binary files (images, video) as bytes.
        """
        target = _safe_artifact_path(runs_dir, run_id, path)
        if not target.is_file():
            raise ValueError(f"artifact not found: {run_id}/{path}")
        return _read_text_or_binary(target)

    @mcp.resource("bajutsu://runs/latest/manifest.json")
    def latest_manifest() -> str:
        """The manifest.json for the most recent run."""
        run = latest_run(runs_dir)
        if run is None:
            raise ValueError("no runs found")
        manifest = run / "manifest.json"
        if not manifest.is_file():
            raise ValueError(f"no manifest in latest run {run.name}")
        return manifest.read_text(encoding="utf-8")

"""`bajutsu serve` — launch the local web UI (Tier 1; not for CI)."""

from __future__ import annotations

from pathlib import Path

import typer


def serve(
    port: int = typer.Option(8765, "--port"),
    config: str = typer.Option(
        "", "--config", help="config to bind at startup; omit to open one from the UI"
    ),
    root: str = typer.Option(
        "", "--root", help="root the UI's file browser may explore (default: current directory)"
    ),
    scenarios: str = typer.Option(
        "", "--scenarios", help="override the app's scenarios dir (default: from config)"
    ),
    runs: str = typer.Option("runs", "--runs", help="runs root to serve reports from"),
    baselines: str = typer.Option(
        "",
        "--baselines",
        help="visual-regression baselines dir (default: a `baselines` folder under --scenarios)",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """Launch a local web UI to run scenarios and view their reports (Tier 1; not for CI).

    Without `--config`, open a config.yml from the UI's file browser (limited to `--root`)."""
    from bajutsu.serve import serve as _serve

    _serve(
        host,
        port,
        Path(scenarios) if scenarios else None,
        Path(config) if config else None,
        Path(runs),
        Path(root) if root else Path.cwd(),
        Path(baselines) if baselines else None,
    )


def register(app: typer.Typer) -> None:
    app.command()(serve)

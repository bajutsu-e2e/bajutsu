"""`bajutsu trace` — inspect a finished run as a text timeline."""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu import trace as _trace


def trace(
    run_dir: str = typer.Argument("", help="run directory (default: the latest under runs/)"),
    scenario: str = typer.Option("", "--scenario", help="only scenarios whose name contains this"),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
) -> None:
    """Inspect a finished run as a text timeline (steps + network + appTrace)."""
    path = Path(run_dir) if run_dir else _trace.latest_run(Path(runs))
    if path is None or not (path / "manifest.json").exists():
        typer.echo(f"no run found{f': {run_dir}' if run_dir else f' under {runs}/'}")
        raise typer.Exit(2)
    typer.echo(_trace.trace_run(path, scenario or None))


def register(app: typer.Typer) -> None:
    app.command()(trace)

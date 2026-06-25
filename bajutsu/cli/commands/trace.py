"""`bajutsu trace` — inspect a finished run as a text timeline, or `--explain` a scenario."""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu import trace as _trace
from bajutsu.cli._shared import load_expanded_scenarios


def trace(
    run_dir: str = typer.Argument(
        "", help="run directory (timeline); with --explain, a scenario file to preview"
    ),
    scenario: str = typer.Option("", "--scenario", help="only scenarios whose name contains this"),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
    explain: bool = typer.Option(
        False,
        "--explain",
        help="dry-run: preview how a scenario's capturePolicy would fire, without running it",
    ),
) -> None:
    """Inspect a finished run as a text timeline (steps + network + appTrace).

    Or — with `--explain` — preview how a scenario's capturePolicy would fire before running it.
    """
    if explain:
        _explain(run_dir)
        return
    path = Path(run_dir) if run_dir else _trace.latest_run(Path(runs))
    if path is None or not (path / "manifest.json").exists():
        typer.echo(f"no run found{f': {run_dir}' if run_dir else f' under {runs}/'}")
        raise typer.Exit(2)
    typer.echo(_trace.trace_run(path, scenario or None))


def _explain(scenario_path: str) -> None:
    """Load a scenario file and print the capturePolicy dry-run report.

    Components + data are expanded, resolved relative to the file. Setup preludes from config are
    not included.
    """
    path = Path(scenario_path)
    if not scenario_path or not path.is_file():
        typer.echo("--explain needs a scenario file path")
        raise typer.Exit(2)
    try:
        scenarios = load_expanded_scenarios(path)
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenario: {e}")
        raise typer.Exit(2) from None
    typer.echo(_trace.render_explain(scenarios))


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(trace)

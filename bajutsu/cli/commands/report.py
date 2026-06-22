"""`bajutsu report` — re-render a finished run's report from its stored data (BE-0068).

Rewrites `runs/<id>/report.html` (and `junit.xml`) for an existing run using the **current**
template, reading the persisted render model (`manifest.json` + `scenario.yaml`) — no device, no
AI, no re-run. So a template improvement or rendering fix reaches past runs without re-executing
them; the verdict is read from the stored model, never recomputed.
"""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu.cli._shared import resolve_run_dir
from bajutsu.report import rebake


def report(
    run: str = typer.Argument("", help="a run id (under --runs) or a run directory path"),
    all_runs: bool = typer.Option(False, "--all", help="re-render every run under --runs"),
    runs: str = typer.Option("runs", help="runs root (used for a bare run id and for --all)"),
) -> None:
    """Re-render a finished run's report.html + junit.xml from its stored data, with the current
    template. Reads only the run dir — no device, no AI, and the verdict is never recomputed."""
    if all_runs == bool(run):  # exactly one of <run> / --all
        typer.echo("give a run (id or path) or --all, not both")
        raise typer.Exit(2)
    if all_runs:
        root = Path(runs)
        targets = sorted(d for d in root.glob("*") if (d / "manifest.json").is_file())
        if not targets:
            typer.echo(f"no runs found under {runs}/")
            raise typer.Exit(2)
    else:
        run_dir = resolve_run_dir(run, runs)
        if not (run_dir / "manifest.json").is_file():
            typer.echo(f"run not found: {run}")
            raise typer.Exit(2)
        targets = [run_dir]
    # A run dir can have a manifest but a missing / corrupt scenario.yaml (a legacy or damaged run);
    # report it cleanly and, under --all, keep going so one bad run doesn't abort the batch.
    failures: list[Path] = []
    for run_dir in targets:
        try:
            rebake(run_dir)
            typer.echo(f"re-rendered {run_dir / 'report.html'}")
        except (OSError, ValueError) as e:
            typer.echo(f"could not re-render {run_dir}: {e}", err=True)
            failures.append(run_dir)
    if failures:
        raise typer.Exit(1 if all_runs else 2)


def register(app: typer.Typer) -> None:
    app.command()(report)

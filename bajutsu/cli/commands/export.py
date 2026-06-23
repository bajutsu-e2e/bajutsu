"""`bajutsu export` — archive a finished run as a single portable zip (BE-0060).

Bundles the whole `runs/<id>/` tree (report.html + its evidence) into one `.zip`, rooted under a
`<id>/` folder so the report's relative links resolve offline. Pure packaging of what the
deterministic run already wrote: no device, no AI, no effect on any verdict.
"""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu.cli._shared import resolve_run_dir
from bajutsu.report.archive import archive_run_dir


def export(
    run: str = typer.Argument(..., help="a run id (under runs/) or a path to a run directory"),
    out: str = typer.Option(
        "", "-o", "--output", help="output zip path (default: <id>.zip beside the run dir)"
    ),
    force: bool = typer.Option(False, "--force", help="overwrite the output file if it exists"),
    runs: str = typer.Option("runs", help="runs root (used when <run> is an id, not a path)"),
) -> None:
    """Archive an existing run into a single `.zip` for sharing / CI / offline viewing."""
    run_dir = resolve_run_dir(run, runs)
    if not run_dir.is_dir():
        typer.echo(f"run not found: {run}")
        raise typer.Exit(2)
    out_path = Path(out) if out else run_dir.parent / f"{run_dir.name}.zip"
    if out_path.exists() and not force:  # never silently overwrite (mirrors record)
        typer.echo(f"refusing to overwrite {out_path} (pass --force)")
        raise typer.Exit(2)
    out_path.write_bytes(archive_run_dir(run_dir))
    typer.echo(f"wrote {out_path}")


def register(app: typer.Typer) -> None:
    app.command()(export)

"""`bajutsu stats` — the aggregate run-stats dashboard (BE-0102).

Read-only and advisory, like `coverage` and `audit`: it aggregates the `manifest.json` of every run
under a directory into one trend — pass-rate over time, run/scenario durations, the scenarios and
steps that fail most, per-scenario flakiness (reused from the BE-0049 audit), and run volume. No
device, no AI, no verdict; it never gates CI. A missing / unreadable runs directory exits 2.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import typer

from bajutsu.analysis import stats as _stats
from bajutsu.cli._shared import read_manifests


def stats(
    runs: str = typer.Option(
        ..., "--runs", help="directory of past runs (each with a manifest.json) to aggregate"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the stats as JSON instead of text"),
    html: str = typer.Option(
        "", "--html", help="write a self-contained HTML dashboard to this path"
    ),
) -> None:
    """Aggregate a directory of runs into the whole-suite trend.

    Reads every run's `manifest.json` under `--runs`, then reports pass-rate over time, durations,
    failure hotspots, flakiness, and volume. Read-only and advisory: it never re-runs an assertion,
    changes a verdict, or gates CI. A missing / unreadable runs directory exits 2.
    """
    runs_dir = Path(runs)
    if not runs_dir.is_dir():
        typer.echo(f"runs directory not found: {runs}")
        raise typer.Exit(2)

    report = _stats.aggregate_runs(read_manifests(runs_dir))

    if html:
        # A confirmation goes to stderr rather than polluting a piped `--json` payload; create the
        # parents of a nested path and fail cleanly (exit 2, like above) on an unwritable location.
        html_path = Path(html)
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(_stats.render_html(report), encoding="utf-8")
        except OSError as e:
            typer.echo(f"failed to write HTML dashboard to {html_path}: {e}", err=True)
            raise typer.Exit(2) from None
        typer.echo(f"wrote HTML stats dashboard: {html_path}", err=True)

    if as_json:
        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
    else:
        typer.echo(_stats.render(report))


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(stats)

"""`bajutsu flakiness` — rank scenarios by cross-run flakiness over the run history (BE-0220, Half 1).

Read-only and advisory, like `audit --history` and `stats`: it groups accumulated runs by their
`provenance.scenarioHash` and ranks each scenario by how much its verdict flips at a constant
fingerprint (`2·min(passed, failed)/runs`), reusing the BE-0049 classification. Two sources feed the
one ranking — `--history <runs-dir>` mines a directory of run manifests (the CI / scripting form),
and the default reads the serve database (`BAJUTSU_DATABASE_URL`), grouping straight from the
provenance stamp on each run row. No device, no AI, no verdict; it never gates CI. A missing runs
directory or an unconfigured database exits 2.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import typer

from bajutsu.cli._shared import read_manifests
from bajutsu.serve.flakiness import (
    FlakinessReport,
    rank_flakiness,
    records_from_manifests,
    render,
)

# The newest-N run window the database read mines, matching the serve panel's `_STATS_RUN_LIMIT` so
# the CLI and the Web UI rank over the same bounded history.
_DB_RUN_LIMIT = 200


def flakiness(
    history: str = typer.Option(
        "",
        "--history",
        help="dir of past runs to mine (each with a manifest.json); omit to read the serve database",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the ranking as JSON instead of text"),
    window: int = typer.Option(
        0, "--window", help="keep only each scenario's newest N runs (0 = the whole history)"
    ),
    org: str = typer.Option(
        "default", "--org", help="org whose runs to mine when reading the database"
    ),
) -> None:
    """Rank scenarios by how much their verdict flips at a constant fingerprint (read-only, advisory).

    Reads either a directory of run manifests (`--history`) or the serve database (the default), then
    prints the suite ranked flaky-first. It never re-runs an assertion, changes a verdict, or gates
    CI. A missing runs directory, an unconfigured database, or a negative `--window` exits 2.
    """
    if window < 0:
        typer.echo("--window must be >= 0")
        raise typer.Exit(2)
    window_runs = window or None
    report = (
        _history_flakiness(history, window_runs) if history else _db_flakiness(org, window_runs)
    )
    if as_json:
        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
    else:
        typer.echo(render(report))


def _history_flakiness(history: str, window_runs: int | None) -> FlakinessReport:
    """Rank a runs directory's manifests — the file-backed source (exits 2 if the dir is missing)."""
    runs_dir = Path(history)
    if not runs_dir.is_dir():
        typer.echo(f"runs directory not found: {history}")
        raise typer.Exit(2)
    records = records_from_manifests(read_manifests(runs_dir))
    return rank_flakiness(records, window_runs=window_runs)


def _db_flakiness(org: str, window_runs: int | None) -> FlakinessReport:
    """Rank the org's serve-database runs — the DB source (exits 2 when none is configured)."""
    from bajutsu.serve.server.db import repository_from_env

    repo = repository_from_env()
    if repo is None:
        typer.echo("no database configured; set BAJUTSU_DATABASE_URL or pass --history <runs-dir>")
        raise typer.Exit(2)
    records = repo.list_runs(org_id=org, limit=_DB_RUN_LIMIT)
    return rank_flakiness(records, window_runs=window_runs)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(flakiness)

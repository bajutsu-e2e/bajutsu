"""`bajutsu approve` — promote a run's captured screenshots to `visual` baselines."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer

from bajutsu import trace as _trace


def approve(
    run_dir: str = typer.Argument("", help="run directory (default: the latest under runs/)"),
    baselines: str = typer.Option(
        ..., "--baselines", help="baselines dir to promote the captured screenshots into"
    ),
    scenario: str = typer.Option(
        "", "--scenario", help="only this scenario id (e.g. 00-home), as in the run dir"
    ),
    all_: bool = typer.Option(
        False, "--all", help="also refresh baselines whose comparison already passed"
    ),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
) -> None:
    """Promote a run's captured screenshots to `visual` baselines.

    By default only failing / missing-baseline visual checks are approved; `--all` also
    refreshes baselines whose comparison passed. Reads the run's manifest.json, so it needs
    no Simulator — pair it with the WebUI's Approve button or use it headless in CI.
    """
    path = Path(run_dir) if run_dir else _trace.latest_run(Path(runs))
    if path is None or not (path / "manifest.json").is_file():
        typer.echo(f"no run found{f': {run_dir}' if run_dir else f' under {runs}/'}")
        raise typer.Exit(2)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    baselines_dir = Path(baselines)
    promoted = 0
    for scn in manifest.get("scenarios", []):
        for a in scn.get("expect_results", []):
            ev = a.get("visual")
            if a.get("kind") != "visual" or not ev or not ev.get("actual"):
                continue
            sid = str(ev["actual"]).split("/", 1)[0]
            if scenario and sid != scenario:
                continue
            if a.get("ok") and not all_:
                continue
            src = path / ev["actual"]
            if not src.is_file():
                typer.echo(f"skip {ev['baseline_name']}: missing {ev['actual']}")
                continue
            dest = baselines_dir / ev["baseline_name"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            typer.echo(f"approved {ev['baseline_name']}  ←  {sid}")
            promoted += 1
    if not promoted:
        typer.echo("nothing to approve (no failing visual checks; use --all to refresh)")
        raise typer.Exit(1)
    typer.echo(f"approved {promoted} baseline(s) → {baselines_dir}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(approve)

"""`bajutsu lint` — validate a scenario file without running it."""

from __future__ import annotations

from pathlib import Path

import typer


def lint(
    scenario: str = typer.Argument(..., help="Path to a scenario *.yaml file"),
    config: str = typer.Option(
        "",
        "--config",
        help="Also check the scenario against its targets in this config: each step's capability, "
        "each setting the route cannot apply, and the preconditions (BE-0467)",
    ),
    target: str = typer.Option(
        "",
        "--target",
        help="The target a scenario declaring no `targets` is checked against (with --config)",
    ),
) -> None:
    """Validate a scenario file without running it.

    Exits 1 on any finding, and 2 when `--config` cannot be checked as asked: a missing config, an
    unknown target, or a scenario declaring no targets with no `--target` to check it against.
    """
    from bajutsu.common.lint import lint_text, provenance_coverage
    from bajutsu.common.scenario import load_scenario_file

    path = Path(scenario)
    if not path.exists():
        typer.echo(f"file not found: {path}")
        raise typer.Exit(1)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        typer.echo(f"read error: {e}")
        raise typer.Exit(1) from e
    # Read the file once and reuse that text for both the validation and the advisory, so the two
    # never see different content (a file edited between two reads).
    errors = lint_text(text)
    if errors:
        for msg in errors:
            typer.echo(msg)
        raise typer.Exit(1)
    if config:
        _lint_targets(text, path, config, target)
    elif target:
        typer.echo("--target needs --config: a target is resolved from the config that names it")
        raise typer.Exit(2)
    typer.echo("ok")
    # Advisory only (BE-0044): report `from:` provenance coverage; never fails the lint.
    advisory = provenance_coverage(load_scenario_file(text).scenarios)
    if advisory is not None:
        typer.echo(advisory)


def _lint_targets(text: str, path: Path, config: str, target: str) -> None:
    """Print every finding of the target checks and exit 1 on any (BE-0467)."""
    from bajutsu.cli._shared import _effective_for, _load_config_with_source
    from bajutsu.common.target_lint import TargetRequired, lint_against_targets

    loaded = _load_config_with_source(config)  # exits 2 on a missing config
    try:
        # `_effective_for` exits 2 on an unknown target, the same refusal `run` gives.
        findings = lint_against_targets(
            text, path, lambda name: _effective_for(loaded, name), target or None
        )
    except TargetRequired as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    except (OSError, ValueError) as e:
        typer.echo(f"failed to expand the scenario: {e}")
        raise typer.Exit(1) from None
    if not findings:
        return
    for f in findings:
        where = f"{path}:{f.line}" if f.line is not None else str(path)
        typer.echo(f"{where}: {f.scenario} [target {f.target}]: {f.message}")
    raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(lint)

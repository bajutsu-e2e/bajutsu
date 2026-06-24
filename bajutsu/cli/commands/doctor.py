"""`bajutsu doctor` — check the environment is runnable and score the current screen."""

from __future__ import annotations

import typer

from bajutsu import env as _env
from bajutsu import idb_version, preflight
from bajutsu.backends import make_driver, select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.doctor import render, score


def doctor(
    target_name: str = typer.Option(..., "--target"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Check the environment is runnable, then score the app's current screen."""
    eff = _load_effective(config, target_name)
    try:
        actuator = select_actuator(_backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    # Runnability gate: the CLIs (+ a booted Simulator) the actuator needs. Fail fast here
    # with a fixable checklist instead of crashing later on a missing tool / no device.
    env_checks = preflight.runnability(actuator, booted_count=lambda: len(_env.booted_udids()))
    # When a pin is declared (defaults.idbVersion), report the installed idb_companion against it
    # so a compatibility break surfaces here, not as a confusing downstream failure (BE-0005).
    # Only probe when a pin exists *and* idb_companion is actually present — runnability already
    # reports a missing companion, so probing it would only spawn a doomed subprocess and print a
    # redundant "installed unknown" line.
    companion_ok = any(c.name == "idb_companion" and c.ok for c in env_checks)
    if actuator == "idb" and eff.idb_version is not None and companion_ok:
        version_check = preflight.idb_version_check(eff.idb_version, idb_version.probe())
        if version_check is not None:
            env_checks.append(version_check)
    if env_checks:
        typer.echo("environment:")
        typer.echo(preflight.render(env_checks))
        if not preflight.passed(env_checks):
            raise typer.Exit(1)
        typer.echo("")
    udid = _env.resolve_udid(udid)
    driver = make_driver(actuator, udid)
    result = score(driver.query(), eff.id_namespaces)
    typer.echo(render(result))
    raise typer.Exit(0 if result.grade != "Blocked" else 1)


def register(app: typer.Typer) -> None:
    app.command()(doctor)

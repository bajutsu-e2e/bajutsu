"""`bajutsu doctor` — check the environment is runnable and score the current screen."""

from __future__ import annotations

import contextlib

import typer

from bajutsu import env as _env
from bajutsu import idb_version, preflight
from bajutsu.backends import make_driver, select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.config import Effective
from bajutsu.doctor import render, score
from bajutsu.drivers import base


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
    # Config gate first: a target missing the field its backend needs (iOS bundleId / web baseUrl)
    # is a usage/config error — fixable without any tool or device — so it exits 2 (distinct from a
    # genuine environment/tool failure, which exits 1) and is surfaced before any doomed probe
    # (BE-0024). `_need_target` rejects a target with neither field at parse time; this catches the
    # wrong field for the selected backend.
    cfg_checks = preflight.config_checks(
        actuator, target=target_name, bundle_id=eff.bundle_id, base_url=eff.base_url
    )
    if not preflight.passed(cfg_checks):
        typer.echo("environment:")
        typer.echo(preflight.render(cfg_checks))
        raise typer.Exit(2)

    # Runnability gate: the CLIs (+ a booted Simulator) the actuator needs. Fail fast here
    # with a fixable checklist instead of crashing later on a missing tool / no device.
    # xcuitest falls back to idb for the screen query, so both tool sets must be present.
    def booted_count() -> int:
        return len(_env.booted_udids())

    env_checks = preflight.runnability(actuator, booted_count=booted_count, web_engine=eff.browser)
    if actuator == "xcuitest":
        idb_checks = preflight.runnability("idb", booted_count=booted_count)
        seen = {c.name for c in env_checks}
        env_checks.extend(c for c in idb_checks if c.name not in seen)
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
    checks = cfg_checks + env_checks
    if checks:
        typer.echo("environment:")
        typer.echo(preflight.render(checks))
        if not preflight.passed(checks):
            raise typer.Exit(1)
        typer.echo("")
    elements = _current_screen(actuator, udid, eff)
    result = score(
        elements,
        eff.id_namespaces,
        ok_coverage=eff.doctor_ok_coverage,
        fail_coverage=eff.doctor_fail_coverage,
    )
    typer.echo(render(result))
    raise typer.Exit(0 if result.grade != "Blocked" else 1)


def _current_screen(actuator: str, udid: str, eff: Effective) -> list[base.Element]:
    """The elements of the screen to score.

    Web (Playwright) has no simctl udid: it navigates a fresh browser to the target's baseUrl (the
    `launch` equivalent) and scores that page, tearing the browser down after. iOS scores whatever
    is on the booted Simulator at the resolved udid.
    """
    if actuator == "playwright":
        if not eff.base_url:
            typer.echo("web target needs baseUrl (set targets.<name>.baseUrl)")
            raise typer.Exit(2)
        # Lazy import keeps Playwright (a heavy optional dep) off the default path.
        from bajutsu.drivers.playwright import PlaywrightDriver, _playwright_error_types

        driver = PlaywrightDriver(eff.base_url, headless=eff.headless, browser=eff.browser)
        try:
            driver.navigate()
            return driver.query()
        finally:
            # Suppress browser-side errors on teardown so a close() failure during a
            # faulted browser does not mask the original navigate/query exception.
            with contextlib.suppress(*_playwright_error_types()):
                driver.close()
    # xcuitest needs a running runner to query, but doctor only scores the current screen —
    # idb can read the same accessibility tree without a runner (BE-0019).
    query_actuator = "idb" if actuator == "xcuitest" else actuator
    return make_driver(query_actuator, _env.resolve_udid(udid)).query()


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(doctor)

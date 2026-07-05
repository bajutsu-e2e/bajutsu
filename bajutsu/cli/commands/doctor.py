"""`bajutsu doctor` — check the environment is runnable and score the current screen."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer

from bajutsu import agents, ai_availability, capability_preflight, idb_version, preflight
from bajutsu import simctl as _simctl
from bajutsu.backends import capabilities_for, make_driver, select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.config import (
    Effective,
    IosConfig,
    ios_bundle_id,
    require_web,
    web_base_url,
    web_engine,
)
from bajutsu.doctor import render, score
from bajutsu.drivers import base
from bajutsu.scenario import load_scenario_file


def check_scenarios(scenario_path: Path, actuator: str) -> list[str]:
    """Check every scenario in *scenario_path* against the backend's capabilities.

    Returns one reason per unsupported construct, prefixed with the scenario name. Pure: no
    device needed — the capability set is a static class constant.

    Note:
        This is a best-effort pre-check on the raw scenario tree. ``use`` components and
        ``data`` row expansion are not applied — they require config context (the component
        library, data sources, ``setup`` steps) that ``doctor --scenario`` does not have
        access to. A capability introduced only through a ``use`` expansion (e.g. a component
        that contains a ``pinch`` step) will not be detected here.

    Raises:
        FileNotFoundError: *scenario_path* does not exist.
    """
    text = scenario_path.read_text(encoding="utf-8")
    scenarios = load_scenario_file(text).scenarios
    caps = capabilities_for(actuator)
    reasons: list[str] = []
    for sc in scenarios:
        reasons.extend(f"[{sc.name}] {r}" for r in capability_preflight.unsupported(sc, caps))
    return reasons


def doctor(
    target_name: str = typer.Option(..., "--target"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    config: str = typer.Option(DEFAULT_CONFIG),
    scenario: str = typer.Option("", "--scenario"),
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
        actuator,
        target=target_name,
        bundle_id=ios_bundle_id(eff),
        base_url=web_base_url(eff),
    )
    if not preflight.passed(cfg_checks):
        typer.echo("environment:")
        typer.echo(preflight.render(cfg_checks))
        raise typer.Exit(2)

    # Capability preflight: when a scenario file is provided, check whether it uses constructs
    # the chosen backend can't perform — pure, no device needed (BE-0024).
    cap_failed = False
    if scenario:
        scenario_path = Path(scenario)
        if not scenario_path.is_file():
            typer.echo(f"scenario not found: {scenario}")
            raise typer.Exit(2)
        cap_reasons = check_scenarios(scenario_path, actuator)
        if cap_reasons:
            cap_failed = True
            typer.echo("capability preflight:")
            for reason in cap_reasons:
                typer.echo(f"  ✘ {reason}")
            typer.echo("")

    # Runnability gate: the CLIs (+ a booted Simulator) the actuator needs. Fail fast here
    # with a fixable checklist instead of crashing later on a missing tool / no device.
    # xcuitest falls back to idb for the screen query, so both tool sets must be present.
    def booted_count() -> int:
        return len(_simctl.booted_udids())

    env_checks = preflight.runnability(
        actuator, booted_count=booted_count, web_engine=web_engine(eff)
    )
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
    ios_pin = (
        eff.platform_config.idb_version if isinstance(eff.platform_config, IosConfig) else None
    )
    if actuator == "idb" and ios_pin is not None and companion_ok:
        version_check = preflight.idb_version_check(ios_pin, idb_version.probe())
        if version_check is not None:
            env_checks.append(version_check)
    checks = cfg_checks + env_checks
    if checks:
        typer.echo("environment:")
        typer.echo(preflight.render(checks))
        # Claude readiness is a distinct, optional section (BE-0101): the deterministic path is
        # graded above and never blocked on it, so it is reported before the environment
        # pass/fail exit and its state never changes the exit code.
        typer.echo("")
        typer.echo(_claude_readiness(eff))
        if not preflight.passed(checks):
            raise typer.Exit(1)
        typer.echo("")
    # Fail after environment is reported, so the user sees both environment and capability issues.
    if cap_failed:
        raise typer.Exit(1)
    elements = _current_screen(actuator, udid, eff)
    result = score(
        elements,
        eff.id_namespaces,
        ok_coverage=eff.doctor_ok_coverage,
        fail_coverage=eff.doctor_fail_coverage,
    )
    typer.echo(render(result))
    raise typer.Exit(0 if result.grade != "Blocked" else 1)


def _claude_readiness(eff: Effective) -> str:
    """The optional Claude-readiness section (BE-0101) — deterministic, LLM-free, never blocking.

    Reads only `ai_availability` against the resolved agent backend / provider. A gap is shown as a
    neutral "not configured (optional)" line, never the ✗ an environment failure uses, so a user
    with no AI setup is never told the deterministic path is broken.
    """
    gap = ai_availability.availability(agent_kind=agents.resolve_kind(), ai=eff.ai)
    if gap is None:
        detail = "reachable"
    else:
        detail = f"not configured (optional) — {ai_availability.message(gap, eff.ai)}"
    return f"Claude (optional):\n  {'✓' if gap is None else '–'} {detail}"


def _current_screen(actuator: str, udid: str, eff: Effective) -> list[base.Element]:
    """The elements of the screen to score.

    Web (Playwright) has no simctl udid: it navigates a fresh browser to the target's baseUrl (the
    `launch` equivalent) and scores that page, tearing the browser down after. iOS scores whatever
    is on the booted Simulator at the resolved udid.
    """
    if actuator == "playwright":
        # A missing baseUrl (or a non-web target forced onto playwright) is a clean, fixable
        # config error, not a crash — `web_base_url` is None for both, so this exits before the
        # require_web narrowing below (which a present baseUrl guarantees is a WebConfig).
        base_url = web_base_url(eff)
        if not base_url:
            typer.echo("web target needs baseUrl (set targets.<name>.baseUrl)")
            raise typer.Exit(2)
        web = require_web(eff)
        # Lazy import keeps Playwright (a heavy optional dep) off the default path.
        from bajutsu.drivers.playwright import PlaywrightDriver, _playwright_error_types

        driver = PlaywrightDriver(base_url, headless=web.headless, browser=web.browser)
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
    return make_driver(query_actuator, _simctl.resolve_udid(udid)).query()


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(doctor)

"""`bajutsu doctor` — check the environment is runnable and score the current screen."""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu import adb as _adb
from bajutsu import capability_preflight, device_errors, preflight
from bajutsu import simctl as _simctl
from bajutsu.agents import availability as ai_availability
from bajutsu.ai import credential_gap
from bajutsu.backends import (
    capabilities_for,
    resolve_actuators,
    select_actuator,
    select_actuator_for_scenario,
)
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.config import (
    Effective,
    android_package,
    idb_version_pin,
    ios_bundle_id,
    web_base_url,
    web_engine,
)
from bajutsu.doctor import DoctorProbeError, probe_screen, render, score
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


def actuator_resolution_summary(eff: Effective, backends: list[str]) -> list[str]:
    """How the target's scenarios resolve across a multi-actuator iOS ladder (BE-0240).

    Informational only — a pure `capability_preflight` pass with no device — so a user can see, up
    front, that `backend: [ios]` will run most scenarios on the cheap actuator and escalate only the
    few whose steps need the richer one. Empty (nothing to disclose) unless the requested backends
    resolve to more than one actuator and the target has a scenarios directory to survey.

    Note:
        Same best-effort caveat as ``check_scenarios``: the raw scenario tree is read without
        ``use`` / ``data`` expansion, so a capability introduced only through a component is not
        counted here.
    """
    if len({*resolve_actuators(backends)}) <= 1 or not eff.evidence_dirs.scenarios:
        return []
    scenarios_dir = Path(eff.evidence_dirs.scenarios)
    if not scenarios_dir.is_dir():
        return []
    tally: dict[str, list[str]] = {}
    for path in sorted(scenarios_dir.glob("*.yaml")):
        for sc in load_scenario_file(path.read_text(encoding="utf-8")).scenarios:
            actuator = select_actuator_for_scenario(backends, sc)
            tally.setdefault(actuator, []).append(sc.name)
    if not tally:
        return []
    lines = ["actuator resolution (per scenario, BE-0240):"]
    for actuator, names in tally.items():
        lines.append(f"  {actuator}: {len(names)} scenario(s)")
    return lines


def doctor(
    target_name: str = typer.Option(..., "--target"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    config: str = typer.Option(DEFAULT_CONFIG),
    scenario: str = typer.Option("", "--scenario"),
) -> None:
    """Check the environment is runnable, then score the app's current screen."""
    eff = _load_effective(config, target_name)
    backends = _backends(backend, eff.backend)
    try:
        actuator = select_actuator(backends)
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
        package=android_package(eff),
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

    # Per-scenario actuator resolution disclosure (BE-0240): when the ladder has more than one iOS
    # actuator, show how the target's scenarios split across them — informational, no device, no gate.
    summary = actuator_resolution_summary(eff, backends)
    if summary:
        for line in summary:
            typer.echo(line)
        typer.echo("")

    # Runnability gate: the CLIs (+ a booted Simulator) the actuator needs. Fail fast here
    # with a fixable checklist instead of crashing later on a missing tool / no device. The
    # xcuitest→idb merge and the idb version-pin check live in the shared assembly so the serve
    # panel reports the same set (BE-0199).
    def booted_count() -> int:
        # Android counts attached adb devices; the iOS backends count booted Simulators.
        if actuator == "adb":
            return len(_adb.booted_serials())
        return len(_simctl.booted_udids())

    env_checks = preflight.doctor_environment_checks(
        actuator,
        booted_count=booted_count,
        web_engine=web_engine(eff),
        ios_pin=idb_version_pin(eff),
    )
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
    # Runnability proved the tools are installed, not that the screen is reachable: a web target
    # whose app server is down still faults on navigate (ERR_CONNECTION_REFUSED). Report it as a
    # fixable error and exit non-zero rather than surfacing a stack trace — doctor diagnoses, it
    # does not crash.
    try:
        elements = _current_screen(actuator, udid, eff)
    except device_errors.DeviceError as e:
        typer.echo(f"could not read the screen to score: {e}")
        raise typer.Exit(1) from None
    result = score(
        elements,
        eff.id_namespaces,
        ok_coverage=eff.doctor_thresholds.ok_coverage,
        fail_coverage=eff.doctor_thresholds.fail_coverage,
    )
    typer.echo(render(result))
    raise typer.Exit(0 if result.grade != "Blocked" else 1)


def _claude_readiness(eff: Effective) -> str:
    """The optional Claude-readiness section (BE-0101) — deterministic, LLM-free, never blocking.

    Reads only the provider's credential gap against the resolved provider. A gap is shown as a
    neutral "not configured (optional)" line, never the ✗ an environment failure uses, so a user with
    no AI setup is never told the deterministic path is broken.
    """
    gap = credential_gap(eff.ai)
    if gap is None:
        detail = "reachable"
    else:
        detail = f"not configured (optional) — {ai_availability.message(gap, eff.ai)}"
    return f"Claude (optional):\n  {'✓' if gap is None else '–'} {detail}"


def _current_screen(actuator: str, udid: str, eff: Effective) -> list[base.Element]:
    """The elements of the screen to score — the shared probe, with the CLI's error UX.

    Maps the probe's config error to `typer.Exit(2)` (a web target with no baseUrl is fixable,
    not a crash); a device/reachability fault raises `DeviceError`, which the caller turns into
    `typer.Exit(1)`.
    """
    try:
        return probe_screen(actuator, udid, eff)
    except DoctorProbeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(doctor)

"""`bajutsu audit` — score a scenario's determinism (BE-0049).

Three modes, all read-only and advisory — they never decide a verdict and never gate CI:

- **Static (default)**: no device, no AI. Grade selectors on the stability ladder, flag over-loose
  waits and coordinate gestures.
- **Repeat-and-diff** (`--repeat K --target <app>`): run the scenario K times under identical
  preconditions and report anything whose outcome varied (`deterministic` vs `flaky`). This is the
  *opposite* of flakiness tolerance / auto-retry — a divergence is a finding to fix, not a retry
  that turns red into green.
- **Longitudinal** (`--history <runs-dir>`): mine accumulated runs, grouping each scenario's
  outcomes by its `provenance.scenarioHash`, and classify any whose verdict flipped at a constant
  fingerprint as flaky. No device, no AI.

A successful audit exits 0 *even with findings*; only a missing / unreadable input (scenario file or
runs dir; or, for `--repeat`, an unavailable target / backend) exits 2.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from bajutsu.analysis import audit as _audit
from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    WORKER_CONFIG_HELP,
    _backends,
    _device_udid_or_exit,
    _effective_for,
    _load_config_with_source,
    _load_device_worker_or_exit,
    _precondition_checks,
    _refuse_bad_preconditions,
    _resolve_browser,
    _select_actuator_or_exit,
    _start_launch_server_or_exit,
    _with_worker_device,
    read_manifests,
)
from bajutsu.common.backends import ensure_web_runtime, select_actuator_for_scenario
from bajutsu.common.config import Effective, web_engine
from bajutsu.common.devices import errors as device_errors
from bajutsu.common.platform_lifecycle import environment_for
from bajutsu.common.run_meta.files import runs_root
from bajutsu.common.run_meta.id import new_run_id
from bajutsu.common.runner import device_pool, run_all
from bajutsu.common.runner.pipeline import with_lifecycle_phases
from bajutsu.common.runner.types import LeaseFn
from bajutsu.common.scenario import Scenario, _scenarios_declaring_targets, load_expanded_scenarios
from bajutsu.run.device_matrix import (
    TargetLanes,
    any_conditions,
    condition_problems,
    installed_browser,
    not_applicable_record,
    read_pool_facts,
    render_not_applicable,
)
from bajutsu.run.device_matrix import plan as plan_device_matrix

if TYPE_CHECKING:
    from collections.abc import Callable

    from bajutsu.common.devices.facts import DeviceFacts


def audit(
    scenario: str = typer.Argument("", help="scenario file to audit (omit with --history)"),
    as_json: bool = typer.Option(False, "--json", help="emit the reports as JSON instead of text"),
    repeat: int = typer.Option(
        0, "--repeat", "-k", help="run the scenario K times and diff outcomes (K>=2); 0 = static"
    ),
    history: str = typer.Option(
        "",
        "--history",
        help="dir of past runs to mine for flakiness, grouping by scenario fingerprint",
    ),
    target_name: str = typer.Option(
        "", "--target", help="app/target to run against (for --repeat)"
    ),
    backend: str = typer.Option("", "--backend"),
    udid: str = typer.Option("booted", "--udid"),
    config: str = typer.Option(DEFAULT_CONFIG, "--config"),
    worker_config: Annotated[
        Path | None, typer.Option("--worker-config", help=WORKER_CONFIG_HELP)
    ] = None,
) -> None:
    """Score a scenario's determinism — statically, by repeated execution, or over run history.

    Three read-only, advisory modes: the static audit (default), repeat-and-diff (`--repeat`), and
    the longitudinal view (`--history <runs-dir>`, which groups accumulated runs by scenario
    fingerprint and flags any whose verdict flipped). None decides a verdict or gates CI. A
    successful audit exits 0 even with findings; a missing input (scenario file, runs dir) or an
    unavailable `--repeat` target / backend exits 2.
    """
    if history:
        if repeat:
            # The longitudinal view reads past runs; --repeat executes new ones. Asking for both is a
            # usage error rather than silently dropping one.
            typer.echo("--history reads past runs and can't be combined with --repeat")
            raise typer.Exit(2)
        if scenario:
            # --history mines a whole runs dir; a positional scenario can't filter it, so taking both
            # is a usage error rather than silently ignoring the scenario the user passed.
            typer.echo("--history mines past runs and takes no scenario argument; omit it")
            raise typer.Exit(2)
        _history_audit(history, as_json)
        return

    if not scenario:
        typer.echo(
            "audit needs a scenario file (or --history <runs-dir> for the longitudinal view)"
        )
        raise typer.Exit(2)
    path = Path(scenario)
    if not path.is_file():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    try:
        scenarios = load_expanded_scenarios(path)
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenario: {e}")
        raise typer.Exit(2) from None

    if repeat and repeat < 2:
        # One run can't be diffed against anything, so a single (or negative) repeat is a usage
        # error rather than a silent fall-through to the static audit.
        typer.echo(
            "--repeat needs K>=2 (one run can't be compared); omit --repeat for the static audit"
        )
        raise typer.Exit(2)
    if repeat >= 2:
        _repeat_audit(scenarios, repeat, target_name, backend, udid, config, as_json, worker_config)
        return

    reports = [_audit.audit_scenario(s) for s in scenarios]
    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
    else:
        typer.echo("\n\n".join(_audit.render(r) for r in reports))


def _history_audit(history: str, as_json: bool) -> None:
    """Mine a runs directory for flakiness — group runs by scenario fingerprint and classify each."""
    runs_dir = Path(history)
    if not runs_dir.is_dir():
        typer.echo(f"runs directory not found: {history}")
        raise typer.Exit(2)
    report = _audit.longitudinal(read_manifests(runs_dir))
    if as_json:
        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
    else:
        typer.echo(_audit.render_longitudinal(report))


def _repeat_audit(
    scenarios: list[Scenario],
    repeat: int,
    target_name: str,
    backend: str,
    udid: str,
    config: str,
    as_json: bool,
    worker_config: Path | None = None,
) -> None:
    """Run each scenario `repeat` times through a real device pool and diff the outcomes."""
    if not target_name:
        typer.echo("--repeat needs --target (the app to run the scenario against)")
        raise typer.Exit(2)
    # `run_all` itself refuses a scenario declaring 2+ targets too — the one chokepoint every
    # caller funnels through — but that guard fires only once device/server startup already ran
    # (bare ValueError, uncaught here). Checking it here first gives `audit` the same clean exit 2
    # `run` gets, before any of that startup cost (BE-0428).
    affected = _scenarios_declaring_targets(scenarios)
    if affected:
        typer.echo(
            "multi-target scenario execution (targets:) is not yet implemented (BE-0428); "
            f"affected scenario(s): {', '.join(affected)}"
        )
        raise typer.Exit(2)
    # A scenario declaring exactly one target must run against that one — the guard above only
    # covers two or more. Without this, `--repeat --target web` on a scenario declaring `targets:
    # [ios]` would pass every check (a lone declared target's steps are always valid against it)
    # and silently run the whole repeat-and-diff against the wrong platform (BE-0428) — the same
    # membership check `run`'s CLI applies to an explicit `--target`.
    mismatched = [s.name for s in scenarios if s.target_names and s.target_names != [target_name]]
    if mismatched:
        typer.echo(
            f"--target '{target_name}' is not one of the declared targets for scenario(s): "
            f"{', '.join(mismatched)}"
        )
        raise typer.Exit(2)
    loaded = _load_config_with_source(config)  # exits 2 on a missing config
    eff = _effective_for(loaded, target_name)  # exits 2 on an unknown target
    # The same precondition checks `run` applies before any device work, against the same targets
    # (BE-0452). Audit has no `--erase`, so each scenario's own erase, then the target's, decides.
    _refuse_bad_preconditions(
        _precondition_checks(loaded, scenarios, target_name, eff),
        erase=None,
    )
    # `with_lifecycle_phases` re-validates its own folded result already, but only `run_all` calls
    # it — deep inside a path whose `finally` below only tears down the device pool / server, not a
    # clean exit 2. A `targets.<name>.hooks` setup/cleanup step carrying a `target` that disagrees
    # with a 0- or 1-target scenario here (the check above only covers `len(targets) >= 2`) would
    # otherwise reach the operator as a raw traceback (BE-0428). Calling it again here, discarding
    # the result, is cheap and means passing this check guarantees `run_all` will too.
    try:
        with_lifecycle_phases(eff, scenarios)
    except ValueError as e:
        typer.echo(f"config-level before/after hook: {e}")
        raise typer.Exit(2) from None
    # Mirror `run`/`doctor`: validate the backend before touching device CLIs, so an unknown /
    # unavailable actuator exits cleanly instead of crashing later.
    worker = _load_device_worker_or_exit(worker_config)
    eff = _with_worker_device(eff, worker)
    actuator, backends = _select_actuator_or_exit(backend, eff, [], worker)
    # The device comes through the target's provider, as in `run`: the `--udid` pool, or the grid
    # an `appium` environment names (BE-0452).
    udid = _device_udid_or_exit(eff, udid)
    try:
        # How a device handle resolves is the platform's, behind the Environment seam (BE-0256); web
        # has no device, so its lane is the synthetic "web".
        udids = (
            ["web"]
            if actuator == "playwright"
            else [environment_for(actuator, udid).resolve_device(udid)]
        )
    except device_errors.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None

    runs, failed = _audit_runs(
        scenarios, eff, actuator, udids, udid, fake=_backends(backend, eff.backend) == ["fake"]
    )
    pools = _RunPools(backends, runs_root() / new_run_id("audit-"))
    shutdown = pools.shutdown

    # Bring up the target's `launchServer` (the web baseUrl host) if declared, like `run` does, so a
    # web target with a server-backed baseUrl can be audited; reused if already serving, torn down
    # in the finally below.
    # Audit is a CLI-only longitudinal tool; serve never spawns it for an uploaded bundle, so it
    # stays ungoverned (upload_exec=None — today's bare-host path). `on_error=shutdown` tears down
    # any device pool already created before exiting if the server fails to come up.
    stop_server, _exec_decision = _start_launch_server_or_exit(eff, on_error=shutdown)
    try:
        # One run at a time, K times each (workers=1 keeps the K runs under identical
        # conditions — the point of the diff). run_dir=None: the audit compares outcomes, not
        # artifacts, so it doesn't write a per-run report tree.
        reports = [
            _named(
                _audit.repeat_diff(
                    run_all(
                        run_eff,
                        [s] * repeat,
                        pools.lease_for(run_eff, run_udids),
                        workers=1,
                        # Mirror `run`: resolve the actuator per scenario (BE-0240), so the
                        # preflight and the pool's lease agree on the cheapest actuator each
                        # repeat runs against.
                        resolve_actuator=lambda scn: select_actuator_for_scenario(backends, scn),
                        # The provider's udid spec is the live-route narrowing signal (BE-0238):
                        # an `appium` grid's endpoint audits against the live capability set,
                        # keeping preflight in lockstep with routing.
                        lease_udid_spec=udid,
                    )
                ),
                label,
            )
            for s, label, run_eff, run_udids in runs
        ]
    except device_errors.DeviceError as e:
        # Parity with `run`/`record`: a device failure (lease/launch) exits 2 cleanly, not a traceback.
        typer.echo(str(e))
        raise typer.Exit(2) from None
    finally:
        # Nested, like `run`'s own outer/inner split (BE-0342): `shutdown()` can now raise a lease's
        # stashed end-of-lease wiring defect, and `stop_server()` must still run — or a defect on one
        # audited scenario leaks the target's `launchServer` process for every audit after it.
        try:
            shutdown()
        finally:
            stop_server()

    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
    else:
        typer.echo("\n\n".join(_audit.render_repeat(r) for r in reports))
    if failed:
        raise typer.Exit(1)


class _RunPools:
    """One device pool per set of devices a run takes, opened on first use (BE-0452).

    Runs on the same devices share a pool, so a scenario's repeats stay on one device.
    """

    def __init__(self, backends: list[str], run_dir: Path) -> None:
        self._backends = backends
        self._run_dir = run_dir
        self._pools: dict[tuple[str, tuple[str, ...]], tuple[LeaseFn, Callable[[], None]]] = {}

    def lease_for(self, run_eff: Effective, run_udids: list[str]) -> LeaseFn:
        """The lease of the pool over *run_udids*, driven as *run_eff* says."""
        key = (web_engine(run_eff), tuple(run_udids))
        if key not in self._pools:
            # A listed engine may not be installed yet; a no-op for a backend other than the web.
            ensure_web_runtime(self._backends, web_engine(run_eff))
            self._pools[key] = device_pool(run_udids, self._backends, run_eff, self._run_dir)
        return self._pools[key][0]

    def shutdown(self) -> None:
        """Shut every pool opened so far down."""
        for _lease, stop in self._pools.values():
            stop()


def _named(report: _audit.RepeatReport, label: str) -> _audit.RepeatReport:
    """*report* named by its run's coordinates too, so two runs of one scenario stay apart."""
    if not label:
        return report
    return dataclasses.replace(report, scenario=f"{report.scenario} [{label}]")


def _audit_runs(
    scenarios: list[Scenario],
    eff: Effective,
    actuator: str,
    udids: list[str],
    udid: str,
    *,
    fake: bool,
    read_facts: Callable[[TargetLanes], list[DeviceFacts]] | None = None,
    read_browser: Callable[[str], DeviceFacts] | None = None,
) -> tuple[list[tuple[Scenario, str, Effective, list[str]]], bool]:
    """The runs `audit --repeat` repeats: one per run the scenarios' `runsOn` conditions form.

    Formed as `run` forms them (BE-0452), each on its own eligible devices. A not-applicable run is
    listed and skipped; a run no device fits is listed and makes the audit exit 1 once it has
    reported the rest. Without a condition, or under `--backend fake`, each scenario is one run on
    the device as given, as before.

    Returns:
        Each run as (scenario, coordinates label, configuration, devices), and whether any failed.
    """
    lanes = {eff.target: TargetLanes(eff.target, eff, actuator, tuple(udids))}
    if fake or not any_conditions(scenarios, lanes, eff.target, engine_flag=False):
        return [(s, "", eff, udids) for s in scenarios], False
    problems = condition_problems(scenarios, {eff.target: eff}, eff.target, engine_flag=False)
    if problems:
        for problem in problems:
            typer.echo(problem)
        raise typer.Exit(2)
    if eff.platform != "web":
        # The pool is the `--udid` list, each entry resolved as the run's own lanes are.
        env = environment_for(actuator, udid)
        pool = tuple(env.resolve_device(u.strip()) for u in udid.split(",") if u.strip())
        lanes = {eff.target: TargetLanes(eff.target, eff, actuator, pool)}
    try:
        matrix = plan_device_matrix(
            scenarios,
            lanes,
            eff.target,
            engine_flag=False,
            read_facts=read_facts or read_pool_facts,
            read_browser=read_browser or installed_browser(eff.backend),
        )
    except device_errors.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    placement = {id(record): cell for cell in matrix.cells for record in cell.runs}
    runs: list[tuple[Scenario, str, Effective, list[str]]] = []
    failed = False
    for record in matrix.records:
        s, combo = scenarios[record.scenario], record.combination
        if combo.outcome == "not-applicable":
            typer.echo(render_not_applicable(not_applicable_record(s, eff.target, combo)))
        elif combo.outcome == "failed":
            name = f"{s.name} [{combo.label}]" if combo.label else s.name
            typer.echo(f"{name}: no device fits this run: {combo.reason}")
            failed = True
        else:
            cell = placement[id(record)]
            engine = cell.engines.get(eff.target)
            run_eff = _resolve_browser(eff, engine) if engine else eff
            runs.append((s, combo.label, run_eff, list(cell.devices.get(eff.target, udids))))
    return runs, failed


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(audit)

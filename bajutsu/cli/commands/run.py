"""`bajutsu run` — execute a scenario deterministically (the Tier-2 CI gate)."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bajutsu.drivers import base

import typer

from bajutsu import device_errors
from bajutsu.analytics import ledger as _usage_ledger
from bajutsu.analytics import usage as _usage
from bajutsu.artifact_perms import make_run_dir
from bajutsu.assertions import GoldenContext
from bajutsu.backends import select_actuator_for_scenario
from bajutsu.cli._projects import config_from_source, open_registry
from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    _ai_redactor,
    _build_alert_locator,
    _load_effective_with_source,
    _log_subsystem_default,
    _resolve_browser,
    _select_actuator_or_exit,
    _start_launch_server_or_exit,
    _with_headed,
)
from bajutsu.config import WEB_ENGINES, Effective, IosConfig
from bajutsu.github import actions as github_actions
from bajutsu.orchestrator import AlertEvent, BlockedHandler, RunResult
from bajutsu.platform_lifecycle import ProvisionProfile, environment_for
from bajutsu.report.archive import archive_run_dir
from bajutsu.report.manifest import _run_backend
from bajutsu.run_id import new_run_id
from bajutsu.runner import device_pool, run_all, run_and_report, run_matrix_and_report
from bajutsu.runner.build import BuildError, build_if_missing
from bajutsu.runner.device_provider import acquire_device
from bajutsu.scenario import (
    DismissAlerts,
    Scenario,
    apply_setups,
    contained_ref,
    dump_mocks,
    expand_components,
    expand_data,
    load_component,
    load_scenario_file,
    load_scenarios,
    read_csv,
    select_scenarios,
)


def _parse_browsers(browsers: str) -> list[str]:
    """Parse `--browsers` into an ordered, de-duplicated engine list, validated against WEB_ENGINES.

    The cross-browser matrix axis (BE-0076): a comma list (`chromium,firefox,webkit`) trimmed of
    blanks and de-duped while keeping order. Empty means no matrix (the run uses the single-engine
    path); `--browsers chromium` is exactly `--browser chromium`. An unknown engine exits 2 — before
    it reaches Playwright — exactly as `--browser` does.

    Raises:
        typer.Exit: an entry isn't one of the known engines (exit code 2).
    """
    engines = list(dict.fromkeys(b.strip() for b in browsers.split(",") if b.strip()))
    for engine in engines:
        if engine not in WEB_ENGINES:
            typer.echo(f"unknown --browsers engine {engine!r}: use any of {', '.join(WEB_ENGINES)}")
            raise typer.Exit(2)
    return engines


def _resolve_lanes(
    actuator: str,
    udid: str,
    workers: int,
    resolve_udid: Callable[[str], str],
) -> tuple[list[str], int]:
    """Resolve the device pool and worker count for the selected actuator.

    Web has no simctl udid: each lane is a near-free BrowserContext, so `--workers N` alone is
    N parallel lanes (BE-0054), keyed by synthetic udids. For idb, `--udid` is a comma list of
    concrete devices and `--workers` is capped to that pool size.
    """
    if actuator == "playwright":
        workers = max(1, workers)
        return [f"web-{i}" for i in range(workers)], workers
    udids = [resolve_udid(u.strip()) for u in udid.split(",") if u.strip()]
    return udids, max(1, min(workers, len(udids)))


def _resolve_dir(
    flag: str, config_value: str | None, scenario_file: Path, default_name: str
) -> Path:
    """Resolve an evidence dir: --flag > config value > `default_name`/ beside the scenario.

    Shared by the baselines / schemas / goldens dirs, which differ only in their config field and
    the directory name used for the scenario-local default.
    """
    if flag:
        return Path(flag)
    elif config_value:
        return Path(config_value)
    else:
        return scenario_file.parent / default_name


def _scenario_files(eff: Effective, scenario: str, target_name: str) -> tuple[list[Path], bool]:
    """The scenario files `run` should load.

    `[--scenario]` when given (an explicit override), else every `*.yaml` in the target's configured
    `scenarios` dir. Returns `(files, single)` where `single` flags the one-file override (so the
    report can carry that file's name/description).
    """
    if scenario:
        path = Path(scenario)
        if not path.exists():
            typer.echo(f"scenario not found: {scenario}")
            raise typer.Exit(2)
        return [path], True
    if eff.evidence_dirs.scenarios is None:
        typer.echo(
            f"target '{target_name}' has no scenarios dir "
            f"(set targets.{target_name}.scenarios, or pass --scenario)"
        )
        raise typer.Exit(2)
    scenarios_dir = Path(eff.evidence_dirs.scenarios)
    if not scenarios_dir.is_dir():
        typer.echo(f"scenarios dir not found: {eff.evidence_dirs.scenarios}")
        raise typer.Exit(2)
    files = sorted(scenarios_dir.glob("*.yaml"))
    if not files:
        typer.echo(f"no scenarios found in {eff.evidence_dirs.scenarios}")
        raise typer.Exit(2)
    return files, False


def _expand_file(path: Path, eff: Effective, root: Path) -> tuple[list[Scenario], str | None]:
    """Load one scenario file and expand its setup/component/data refs.

    Each ref is resolved relative to THIS file's directory, so a multi-file dir run keeps every
    file's refs local. Component and data refs are confined to *root* (the suite dir, or the file's
    own dir for a single-file run), so a scenario cannot read outside its suite (BE-0174). Returns
    the expanded scenarios plus the file-level description.
    """
    scenario_file = load_scenario_file(path.read_text(encoding="utf-8"))
    scenarios = scenario_file.scenarios
    # Refs (setup/use/data) resolve relative to this scenario file's own directory.
    base_dir = path.parent
    try:
        apply_setups(
            scenarios,
            eff.setup,
            lambda ref: load_scenarios((base_dir / ref).read_text(encoding="utf-8"))[0].steps,
        )
    except (OSError, ValueError, IndexError) as e:
        typer.echo(f"setup の読み込みに失敗: {e}")
        raise typer.Exit(2) from None
    try:
        expand_components(
            scenarios,
            lambda ref: load_component(
                contained_ref(root, base_dir, ref).read_text(encoding="utf-8")
            ),
        )
    except (OSError, ValueError) as e:
        typer.echo(f"component の展開に失敗: {e}")
        raise typer.Exit(2) from None
    try:
        scenarios = expand_data(
            scenarios,
            lambda ref: read_csv(contained_ref(root, base_dir, ref).read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as e:
        typer.echo(f"data の展開に失敗: {e}")
        raise typer.Exit(2) from None
    return scenarios, scenario_file.description


def _resolve_config_and_engines(
    config: str,
    target_name: str,
    *,
    offline: bool,
    require_pinned: bool,
    headed: bool | None,
    browser: str,
    browsers: str,
) -> tuple[Effective, dict[str, str] | None, list[str]]:
    """Resolve the effective config (building a Git-sourced app on demand) and the engine list.

    Applies `--headed` and `--browser`, then parses `--browsers` into the cross-browser matrix axis
    (BE-0076). Returns the resolved config, its Git source provenance (None for a local config), and
    the requested engines exactly as `--browsers` gave them: empty when `--browsers` is absent, a
    single entry — already collapsed onto `eff.browser`, the single-engine path — for one engine, or
    every listed engine for several. Only `len(...) > 1` takes the matrix path downstream.
    """
    eff, config_source, checkout_root = _load_effective_with_source(
        config, target_name, offline=offline, require_pinned=require_pinned
    )
    # A Git-sourced config is fetched into a content-addressed checkout that holds no built binary,
    # with no chance to build it by hand first — so build it on demand from the checkout root (where
    # the config's `build` command is rooted). Local configs keep today's behavior: launch errors if
    # the binary is missing (BE-0063).
    if checkout_root is not None and isinstance(eff.platform_config, IosConfig):
        try:
            build_if_missing(
                eff.platform_config.build, eff.platform_config.app_path, cwd=checkout_root
            )
        except BuildError as e:
            typer.echo(str(e))
            raise typer.Exit(2) from None
    # --headed/--no-headed overrides the target's `headless` config (web backend only; iOS ignores it).
    eff = _with_headed(eff, headed)
    # --browser overrides the target's `browser` config (web backend only; flag > config > chromium).
    eff = _resolve_browser(eff, browser)
    # --browsers is the multi-engine spelling of the same axis: a comma list fans the run out across
    # engines into a matrix. One engine collapses to the single-engine path (set as --browser would);
    # >1 takes the matrix branch. Validated up front (unknown → 2).
    engines = _parse_browsers(browsers)
    if len(engines) == 1:
        eff = _resolve_browser(eff, engines[0])
    return eff, config_source, engines


def _resolve_secrets(eff: Effective) -> tuple[dict[str, str], list[str]]:
    """Resolve declared secrets from the environment into ${secrets.X} bindings and mask values.

    Only secrets actually present in the environment are bound. The literal values are collected so
    evidence and run-level artifacts can mask them (the scenario definition keeps the token, never
    the value).
    """
    bindings = {f"secrets.{n}": os.environ[n] for n in eff.secrets if n in os.environ}
    return bindings, list(bindings.values())


def _load_scenarios(
    eff: Effective, scenario: str, target_name: str
) -> tuple[list[Scenario], str | None, str, list[Path]]:
    """Load and fully expand the run's scenarios: the `--scenario` file, or the target's dir.

    Each file's setup/component/data refs resolve relative to its own directory, then the expanded
    scenarios concatenate into one run. Returns the scenarios, the single-file description (None for
    a directory run), the report's source label, and the source files (for directory resolution).
    """
    files, single = _scenario_files(eff, scenario, target_name)
    # The containment root for refs: the configured scenarios dir for a suite run, or the single
    # file's own directory for a `--scenario` override (BE-0174).
    root = files[0].parent if single else Path(eff.evidence_dirs.scenarios or files[0].parent)
    scenarios: list[Scenario] = []
    description: str | None = None
    for path in files:
        expanded, file_desc = _expand_file(path, eff, root)
        scenarios.extend(expanded)
        if single:
            description = file_desc
    # The report's source label: the single file's name, or the dir name for a config-driven run.
    source_name = files[0].name if single else Path(eff.evidence_dirs.scenarios or "").name
    return scenarios, description, source_name, files


def _filter_scenarios(
    scenarios: list[Scenario], tag: str, exclude: str, erase: bool | None, target_erase: bool
) -> list[Scenario]:
    """Apply `--tag`/`--exclude` selection and resolve each scenario's `preconditions.erase`.

    Selection runs over the combined set; an empty result is a usage error (exit 2). Erase resolves
    most-specific-wins (BE-0177): `--erase` / `--no-erase` overrides every scenario, else a scenario's
    own explicit value, else *target_erase* (the target config default, already the built-in off when
    unset). Leaves every scenario with a concrete bool, so downstream never sees the unset `None`.
    """
    include = [t.strip() for t in tag.split(",") if t.strip()]
    excluded = [t.strip() for t in exclude.split(",") if t.strip()]
    if include or excluded:
        scenarios = select_scenarios(scenarios, include, excluded)
        if not scenarios:
            typer.echo("no scenarios match --tag/--exclude")
            raise typer.Exit(2)
    for s in scenarios:
        if erase is not None:
            s.preconditions.erase = erase  # CLI flag overrides every scenario
        elif s.preconditions.erase is None:
            s.preconditions.erase = target_erase  # unset scenario inherits the target default
    return scenarios


def _select_actuator(backend: str, eff: Effective, engines: list[str]) -> tuple[str, list[str]]:
    """Select the actuator for the requested backends, provisioning any web runtime, then validate.

    Validates the backend before touching the Simulator CLIs, so an unknown/unavailable actuator
    exits cleanly (2) rather than crashing on a missing `xcrun`/`simctl` — mirroring `doctor`.
    Auto-installs Playwright and each requested engine for a web run (idempotent). A multi-engine
    `--browsers` matrix on a non-web actuator is a user error caught up front. Returns the resolved
    actuator and the ordered backend list.
    """
    actuator, backends = _select_actuator_or_exit(backend, eff, engines)
    # --browsers is a web-only axis: a multi-engine matrix on a non-web actuator is a user error,
    # caught up front rather than after building an iOS pool that ignores the engine list.
    if len(engines) > 1 and actuator != "playwright":
        typer.echo(f"--browsers is web-only; backend '{actuator}' has a single engine")
        raise typer.Exit(2)
    return actuator, backends


def _apply_dismiss_alerts(scenarios: list[Scenario], dismiss_alerts: bool | None) -> None:
    """Apply the `--dismiss-alerts` / `--no-dismiss-alerts` override to every scenario's guard.

    Preserves any per-scenario instruction; a no-op when the flag is unset (each scenario's own
    `dismissAlerts`, default on, decides). Mirrors the `--erase` override.
    """
    if dismiss_alerts is None:
        return
    for s in scenarios:
        instr = s.dismiss_alerts.instruction if s.dismiss_alerts else None
        s.dismiss_alerts = DismissAlerts(enabled=dismiss_alerts, instruction=instr)


def _alert_guard_factory(
    scenarios: list[Scenario], eff: Effective, alert_instruction: str
) -> Callable[[Scenario], BlockedHandler | None] | None:
    """Build a per-scenario alert-guard factory, or None when no scenario wants a guard.

    The vision locator is shared (one client); each scenario gets its own guard so its instruction
    applies. Best-effort: the vision guard reaches Claude through the configured AI provider
    (BE-0053/BE-0047), so a missing/insufficient credential prints a note and no-ops — never a client
    that would fall back to a hosted default, so the deterministic gate still runs Claude-free.
    """

    # A scenario's guard is on when its own `dismissAlerts` says so, else the target config's, else the
    # built-in on (BE-0177). The `--dismiss-alerts` flag is already baked onto the scenario by
    # `_apply_dismiss_alerts`, so it needs no separate check here.
    def _enabled(s: Scenario) -> bool:
        if s.dismiss_alerts is not None:
            return s.dismiss_alerts.enabled
        if eff.run_defaults.dismiss_alerts is not None:
            return eff.run_defaults.dismiss_alerts.enabled
        return True

    if not any(_enabled(s) for s in scenarios):
        return None
    from bajutsu.agents.alerts import SystemAlertGuard

    # One shared locator across the per-scenario guards (one client), None when the credential is
    # missing (the shared helper prints the note and no-ops, so the guard never falls back). Mask the
    # (possibly user-supplied) alert instruction before it reaches the model (BE-0047).
    redactor = _ai_redactor(eff)
    locator = _build_alert_locator(eff, redactor)
    default_instruction = alert_instruction or None

    # The button label resolves scenario > `--alert-instruction` > target config > built-in dismissive
    # (BE-0177): a scenario's own wins, then the run-wide flag default, then the app default, then None
    # (the guard's built-in). `--alert-instruction` stays a *default* the scenario overrides, as before.
    target_instruction = (
        eff.run_defaults.dismiss_alerts.instruction if eff.run_defaults.dismiss_alerts else None
    )

    def _guard_for(s: Scenario) -> BlockedHandler | None:
        if locator is None:
            return None  # no usable AI credential: the guard no-ops, never a hosted fallback
        if not _enabled(s):
            return None
        scenario_instruction = s.dismiss_alerts.instruction if s.dismiss_alerts else None
        # Trailing `or None` normalizes an empty instruction (e.g. config `instruction: ""`) to the
        # guard's built-in default, matching how `default_instruction` drops an empty --alert-instruction.
        instruction = scenario_instruction or default_instruction or target_instruction or None
        handler = SystemAlertGuard(locator, instruction).dismiss

        # Attribute the guard's AI tokens/cost to this scenario (BE-0196). The handler fires inside
        # the runner's `ThreadPoolExecutor` worker, so the scope must be entered *there* — a contextvar
        # bound on the main thread would not reach the worker under `run --workers N`.
        def _attributed(driver: base.Driver) -> AlertEvent | None:
            with _usage_ledger.attributed(command="run", scenario=s.name):
                return handler(driver)

        return _attributed

    return _guard_for


def _resolve_network(network: bool | None, target_network: bool) -> bool:
    """Resolve network collection: `--network/--no-network` flag > target `network` config > on (BE-0177)."""
    return network if network is not None else target_network


def _apply_mocks(scenarios: list[Scenario], network: bool) -> None:
    """Bake each scenario's mocks into its launch env so BajutsuKit stubs matching requests.

    Mocks ride the network channel (so the network is deterministic, and still observed) — a no-op
    under `--no-network`. They're per-scenario and device-independent; the per-device collector url
    is injected by the pool at lease time.
    """
    if not network:
        return
    for s in scenarios:
        if s.mocks:
            s.preconditions.launch_env.setdefault("BAJUTSU_MOCKS", dump_mocks(s.mocks))


def _resolve_evidence_dirs(
    baselines: str, schemas: str, goldens: str, eff: Effective, scenario_file: Path
) -> tuple[Path, Path, GoldenContext | None]:
    """Resolve the baselines / schemas directories and the golden context (flag > config > default).

    Each follows --flag > config > dir-beside-the-scenario. The golden context is built only when the
    goldens dir exists, so `golden` assertions can resolve their `path` within it.
    """
    baselines_dir = _resolve_dir(baselines, eff.evidence_dirs.baselines, scenario_file, "baselines")
    schemas_dir = _resolve_dir(schemas, eff.evidence_dirs.schemas, scenario_file, "schemas")
    goldens_dir = _resolve_dir(goldens, eff.evidence_dirs.goldens, scenario_file, "goldens")
    gc = GoldenContext(goldens_dir=goldens_dir) if goldens_dir.is_dir() else None
    return baselines_dir, schemas_dir, gc


@dataclass(frozen=True)
class _RunPlan:
    """Everything a resolved `run` needs to dispatch and report — plain data, no behavior.

    `run` fills this from the option flags via the `_resolve_*`/`_load_*` helpers, then hands it to
    `_dispatch` and `_finish`. It carries resolved inputs only (no methods, no `self`-mutation), so
    each helper stays unit-testable without a Simulator.
    """

    eff: Effective
    config_source: dict[str, str] | None
    target_name: str
    scenarios: list[Scenario]
    description: str | None
    source_name: str
    engines: list[str]
    actuator: str
    backends: list[str]
    udids: list[str]
    # The provider's raw udid spec for this run (`lease.udid_spec`): a WebDriver URL routes to the
    # live XCUITest environment, so the pipeline's preflight narrows to that transport's set (BE-0238).
    udid_spec: str
    workers: int
    # The device provider's readiness report for this run (BE-0236); the pool threads it to each
    # environment so a cloud-provisioned device can skip its boot wait / install.
    provision: ProvisionProfile
    on_blocked_for: Callable[[Scenario], BlockedHandler | None] | None
    baselines_dir: Path
    schemas_dir: Path
    golden_context: GoldenContext | None
    secret_bindings: dict[str, str]
    secret_values: list[str]
    run_id: str
    runs_dir: Path
    network: bool
    log_predicate: str
    log_subsystem: str
    progress: bool
    zip_run: bool
    evidence_store: str
    upload_exec: str


def _dispatch(plan: _RunPlan) -> tuple[list[RunResult], Path]:
    """Bring up the launch server and execute the run — single-engine or cross-browser matrix.

    The launch server (if the target declares one) is brought up before the pool leases and torn
    down in the finally; one server serves every engine in a matrix run. Returns the per-scenario
    results and the report manifest path.
    """
    # --progress streams scenario/step lines to stderr (the web UI merges them into its run log);
    # stdout stays the machine-readable final PASS/FAIL line.
    progress_fn = (
        (lambda msg: print(msg, file=sys.stderr, flush=True)) if plan.progress else None  # noqa: T201
    )
    # Webhook: 'start' notification for endpoints that subscribe to it (BE-0099).
    if plan.eff.notify:
        from bajutsu import notify

        notify.emit_start(
            run_id=plan.run_id,
            source_name=plan.source_name,
            target=plan.target_name,
            scenario_count=len(plan.scenarios),
            endpoints=plan.eff.notify,
            bindings=plan.secret_bindings,
        )
    # Bring up the app's target server (the web baseUrl host) if it declares `launchServer`, waiting
    # on its readiness probe; reused if already serving. The pool leases lazily (the web driver
    # navigates at lease time), so the server only needs to be up before the run, not before the pool.
    stop_server, exec_decision = _start_launch_server_or_exit(
        plan.eff, upload_exec=plan.upload_exec or None
    )
    try:
        if len(plan.engines) > 1:
            return _dispatch_matrix(plan, progress_fn, exec_decision)
        return _dispatch_single(plan, progress_fn, exec_decision)
    except device_errors.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    finally:
        stop_server()


def _dispatch_single(
    plan: _RunPlan,
    progress_fn: Callable[[str], None] | None,
    exec_decision: dict[str, str | None] | None,
) -> tuple[list[RunResult], Path]:
    """The single-engine path — exactly today's flow: one pool, one `run_and_report`, no matrix."""
    # Own the run dir owner-only before the pool can create anything under it (e.g. Playwright's
    # `_video_tmp`), so no world-readable window exists before the pipeline's own chmod (BE-0131).
    make_run_dir(plan.runs_dir / plan.run_id)
    lease, shutdown = device_pool(
        plan.udids,
        plan.backends,
        plan.eff,
        plan.runs_dir / plan.run_id,
        network=plan.network,
        log_predicate=plan.log_predicate or None,
        log_subsystem=plan.log_subsystem or _log_subsystem_default(plan.eff),
        secret_values=plan.secret_values,
        provision=plan.provision,
    )
    try:
        return run_and_report(
            plan.eff,
            plan.scenarios,
            lease,
            plan.runs_dir,
            plan.run_id,
            on_blocked_for=plan.on_blocked_for,
            workers=plan.workers,
            bindings=plan.secret_bindings,
            secret_values=plan.secret_values,
            source_name=plan.source_name,
            description=plan.description,
            progress=progress_fn,
            baselines_dir=plan.baselines_dir,
            schemas_dir=plan.schemas_dir,
            # Per-scenario actuator selection (BE-0240): the pipeline preflights, and the pool leases,
            # the cheapest actuator each scenario can run on — a single `[xcuitest]`/`[web]` pin still
            # collapses to that one actuator; a multi-actuator platform escalates only the scenarios
            # that need it (iOS is single-actuator since BE-0290, so `[ios]` collapses too).
            resolve_actuator=lambda s: select_actuator_for_scenario(plan.backends, s),
            config_source=plan.config_source,
            exec_provenance=exec_decision,
            golden_context=plan.golden_context,
            lease_udid_spec=plan.udid_spec,
        )
    finally:
        shutdown()


def _dispatch_matrix(
    plan: _RunPlan,
    progress_fn: Callable[[str], None] | None,
    exec_decision: dict[str, str | None] | None,
) -> tuple[list[RunResult], Path]:
    """The cross-browser matrix (BE-0076): one pass per engine against its own pool.

    Evidence lands under run_dir/<engine>/<sid>; the pipeline assembles ONE report whose matrix
    aggregates the per-engine verdicts (all-must-pass, machine-only).
    """
    # Own the top run dir owner-only before any engine pool can create a subdir under it, so every
    # engine's evidence sits beneath a non-world-readable parent from the first write (BE-0131).
    make_run_dir(plan.runs_dir / plan.run_id)

    def run_pass(engine: str, engine_run_dir: Path) -> list[RunResult]:
        if progress_fn is not None:
            progress_fn(f"━ engine {engine}")
        eff_e = _resolve_browser(plan.eff, engine)
        lease, shutdown = device_pool(
            plan.udids,
            plan.backends,
            eff_e,
            engine_run_dir,
            network=plan.network,
            log_predicate=plan.log_predicate or None,
            log_subsystem=plan.log_subsystem or _log_subsystem_default(eff_e),
            secret_values=plan.secret_values,
            provision=plan.provision,
        )
        try:
            return run_all(
                eff_e,
                plan.scenarios,
                lease,
                on_blocked_for=plan.on_blocked_for,
                workers=plan.workers,
                run_dir=engine_run_dir,
                bindings=plan.secret_bindings,
                secret_values=plan.secret_values,
                progress=progress_fn,
                baselines_dir=plan.baselines_dir,
                schemas_dir=plan.schemas_dir,
                actuator=plan.actuator,
                golden_context=plan.golden_context,
            )
        finally:
            shutdown()

    return run_matrix_and_report(
        plan.eff,
        plan.scenarios,
        plan.engines,
        run_pass,
        plan.runs_dir,
        plan.run_id,
        source_name=plan.source_name,
        description=plan.description,
        secret_values=plan.secret_values,
        config_source=plan.config_source,
        exec_provenance=exec_decision,
    )


def _write_zip(manifest: Path) -> None:
    """Package the finished run into runs/<id>.zip, strictly after the verdict (BE-0060).

    A write failure (disk full, permissions) must not flip the verdict, so it warns on stderr rather
    than raising; stdout stays the PASS/FAIL line.
    """
    run_dir = manifest.parent
    zip_path = run_dir.parent / f"{run_dir.name}.zip"
    try:
        zip_path.write_bytes(archive_run_dir(run_dir))
        typer.echo(f"wrote {zip_path}", err=True)
    except OSError as e:
        typer.echo(f"warning: --zip failed ({e}); the run verdict stands", err=True)


def _upload_evidence(manifest: Path, evidence_store: str) -> None:
    """Upload the finished run tree to object storage, strictly after the verdict (BE-0110).

    object_store is imported lazily so the default path never loads the cloud SDKs. Any failure — a
    bad URI, a missing SDK, or missing/denied credentials — warns and never flips the exit code.
    """
    from bajutsu.object_store import object_store_from_uri, parse_store_uri, upload_tree

    run_dir = manifest.parent
    try:
        uri = parse_store_uri(evidence_store)
        summary = upload_tree(object_store_from_uri(uri), run_dir, uri.prefix)
    except Exception as e:  # a bad URI, a missing SDK, or missing/denied credentials — any of these
        # must warn, never flip the already-final verdict (BE-0110). Client construction (e.g. GCS
        # ADC) can raise SDK-specific errors, not just ValueError/ImportError.
        typer.echo(f"warning: --evidence-store failed ({e}); the run verdict stands", err=True)
    else:
        typer.echo(
            f"uploaded {summary.uploaded} file(s) to {evidence_store}"
            + (f"; {len(summary.failures)} failed" if summary.failures else ""),
            err=True,
        )
        for key, reason in summary.failures:
            typer.echo(f"  warning: upload failed for {key}: {reason}", err=True)


def _finish(
    plan: _RunPlan, results: list[RunResult], manifest: Path, before: _usage.TokenUsage
) -> None:
    """Emit the verdict and every post-verdict step, then exit with the machine-only code.

    Order is load-bearing: the PASS/FAIL verdict and exit code are decided first (machine-only, no
    LLM); `--zip`, `--evidence-store`, and the AI usage summary all run strictly after and can only
    warn, never flip the verdict (BE-0060/BE-0110).
    """
    ok = all(r.ok for r in results)
    github_actions.emit(results, manifest.parent / "report.html")  # annotations + summary in CI
    # Webhook: post-verdict notification (BE-0099).
    if plan.eff.notify:
        from bajutsu import notify

        # Actuator selection is per scenario (BE-0240), so report the distinct actuators that
        # actually ran — joined when a run mixed idb and XCUITest — not the single pool pick; fall
        # back to `plan.actuator` when every scenario failed before an actuator drove it. Reuses the
        # manifest's join so the dedup/order/empty-filter semantics live in one place (report/html.py
        # already imports it across the boundary the same way).
        ran = _run_backend(results)
        notify.emit(
            results,
            run_id=plan.run_id,
            source_name=plan.source_name,
            backend=ran or plan.actuator,
            endpoints=plan.eff.notify,
            bindings=plan.secret_bindings,
            runs_dir=plan.runs_dir,
        )
    typer.echo(f"{'PASS' if ok else 'FAIL'}  {manifest}")
    if plan.zip_run:
        _write_zip(manifest)
    if plan.evidence_store:
        _upload_evidence(manifest, plan.evidence_store)
    # The only AI in `run` is the alert guard (when it actually fired). Report its token use on
    # stderr so stdout stays the machine-readable PASS/FAIL line; silent when nothing fired.
    spent = _usage.snapshot() - before
    if spent.calls:
        typer.echo(spent.render(), err=True)
    raise typer.Exit(0 if ok else 1)


def _resolve_project_config(project: str, runs_dir: str) -> str:
    """The `--config` spec for the named project — so `run --project X` is `run --config <X's source>`.

    Reads the same project store the `project` subcommands and `serve` share (the DB when
    `BAJUTSU_DATABASE_URL` is set, else the on-disk JSON beside *runs_dir*), resolves the project in
    the local `default` org, and reconstructs its config spec. Unlike the API trigger — which runs the
    serve-bound active project only — the CLI is stateless and resolves the config fresh each call, so
    it runs any named project without a switch.
    """
    from bajutsu.serve.orgs import DEFAULT_ORG

    registry = open_registry(runs_dir)
    record = registry.get(org_id=DEFAULT_ORG, name=project)
    if record is None:
        raise typer.BadParameter(f"no project named {project!r}", param_hint="--project")
    try:
        return config_from_source(record.source)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--project") from e


def run(
    # --- Target & scenario selection ---
    target_name: str = typer.Option(..., "--target"),
    scenario: str = typer.Option(
        "",
        "--scenario",
        help="run only this one *.yaml (overrides the target's configured scenarios dir)",
    ),
    backend: str = typer.Option(
        "",
        help="comma list of platforms (ios/android/web/fake) or actuators (idb); first available wins",
    ),
    tag: str = typer.Option(
        "", "--tag", help="comma list; run only scenarios with any of these tags"
    ),
    exclude: str = typer.Option(
        "", "--exclude", help="comma list; skip scenarios with any of these tags"
    ),
    # --- Backend & device selection ---
    udid: str = typer.Option("booted"),
    workers: int = typer.Option(1),
    erase: bool | None = typer.Option(
        None,
        "--erase/--no-erase",
        help="override every scenario's preconditions.erase (default: per-scenario)",
    ),
    # --- Alerts, capture & logging ---
    dismiss_alerts: bool | None = typer.Option(
        None,
        "--dismiss-alerts/--no-dismiss-alerts",
        help="override every scenario's dismissAlerts (default: per-scenario, on; needs the "
        "configured AI provider — ANTHROPIC_API_KEY, or AWS credentials for Bedrock)",
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="default button instruction (a scenario's own wins)"
    ),
    log_predicate: str = typer.Option(
        "", "--log-predicate", help="NSPredicate narrowing the deviceLog stream (e.g. subsystem)"
    ),
    log_subsystem: str = typer.Option(
        "", "--log-subsystem", help="os_log subsystem for appTrace (defaults to the app's bundleId)"
    ),
    network: bool | None = typer.Option(
        None,
        "--network/--no-network",
        help="collect the app's network exchanges (for `request` assertions); iOS needs BajutsuKit "
        "in the app, web (Playwright) observes natively. Default: the target's `network` config, "
        "then on",
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="stream per-scenario/step progress to stderr as the run advances (used by the web UI)",
    ),
    # --- Baseline / schema / golden directory overrides ---
    baselines: str = typer.Option(
        "",
        "--baselines",
        help="directory of baseline images for `visual` assertions "
        "(default: config baselines, then baselines/ beside the scenario)",
    ),
    schemas: str = typer.Option(
        "",
        "--schemas",
        help="directory of JSON Schema files for `responseSchema` assertions "
        "(default: config schemas, then schemas/ beside the scenario)",
    ),
    goldens: str = typer.Option(
        "",
        "--goldens",
        help="directory of golden JSON files for `golden` assertions (BE-0006) "
        "(default: goldens/ beside the scenario)",
    ),
    # --- Browser & engine selection ---
    headed: bool | None = typer.Option(
        None,
        "--headed/--no-headed",
        help="web backend: show the browser (headed, slow-motion) instead of headless; "
        "default leaves the target's `headless` config (headless)",
    ),
    browser: str = typer.Option(
        "",
        "--browser",
        help=f"web backend: rendering engine to drive — {' / '.join(WEB_ENGINES)}; "
        "default leaves the target's `browser` config (chromium)",
    ),
    browsers: str = typer.Option(
        "",
        "--browsers",
        help=f"web backend: run the cross-browser matrix — a comma list of engines "
        f"({','.join(WEB_ENGINES)}); each scenario runs once per engine and the run is green only "
        "if every engine passes (all-must-pass). A single engine equals --browser",
    ),
    # --- Reporting & output ---
    zip_run: bool = typer.Option(
        False,
        "--zip",
        help="after the run, also write runs/<id>.zip — one portable artifact (report + evidence) "
        "for CI upload or sharing; runs after the verdict, so it can't affect pass/fail",
    ),
    runs_dir: str = typer.Option(
        "runs",
        "--runs-dir",
        help="directory to write the run tree into (default: ./runs). Lets a caller run from one "
        "working directory but persist the run elsewhere — e.g. serve running an uploaded bundle "
        "from its extracted dir while keeping the run in serve's store (BE-0073)",
    ),
    evidence_store: str = typer.Option(
        "",
        "--evidence-store",
        envvar="BAJUTSU_EVIDENCE_STORE",
        help="after the run, upload the run tree to object storage at this URI "
        "(s3://bucket/prefix or gs://bucket/prefix); the upload path picks the cloud lifecycle "
        "policy. Runs after the verdict, so an upload failure can't affect pass/fail (BE-0110). "
        "Needs the s3 or gcs extra",
    ),
    upload_exec: str = typer.Option(
        "",
        "--upload-exec",
        hidden=True,
        help="internal: serve sets this for an uploaded bundle to govern its launchServer command "
        "(deny | reuse | sandbox); empty = ungoverned local/Git run (BE-0090)",
    ),
    # --- Config sourcing ---
    config: str = typer.Option(DEFAULT_CONFIG),
    project: str = typer.Option(
        "",
        "--project",
        help="run a project registered with `bajutsu project add` by name — resolves its config "
        "source and runs it, the headless trigger a CI/cron step calls (BE-0225). Mutually "
        "exclusive with --config",
    ),
    config_offline: bool = typer.Option(
        False,
        "--config-offline",
        help="for a Git --config: use the cache, never touch the network (needs a pinned @<sha>)",
    ),
    require_pinned_config: bool = typer.Option(
        False,
        "--require-pinned-config",
        help="for a Git --config: fail unless it pins a commit SHA (a branch/tag can move — for a gate)",
    ),
) -> None:
    """Run a scenario deterministically.

    Pass/fail is machine-only; the sole AI is the alert guard (on by default per scenario), which
    only fires to clear an OS prompt that blocked a step — see each scenario's `dismissAlerts`.
    """
    # `--project` names a registered project; resolve its config source into the ordinary `--config`
    # spec so the rest of the run path is unchanged — a project only says where the config comes from.
    if project:
        if config != DEFAULT_CONFIG:
            raise typer.BadParameter(
                "pass one of --project or --config, not both", param_hint="--project"
            )
        config = _resolve_project_config(project, runs_dir)
    # Resolve the run's inputs from the flags — each step is an independently testable helper — then
    # assemble the plan and hand it to dispatch/finish. `run` itself stays a thin sequence.
    eff, config_source, engines = _resolve_config_and_engines(
        config,
        target_name,
        offline=config_offline,
        require_pinned=require_pinned_config,
        headed=headed,
        browser=browser,
        browsers=browsers,
    )
    secret_bindings, secret_values = _resolve_secrets(eff)
    scenarios, description, source_name, files = _load_scenarios(eff, scenario, target_name)
    scenarios = _filter_scenarios(scenarios, tag, exclude, erase, eff.run_defaults.erase)
    actuator, backends = _select_actuator(backend, eff, engines)
    # Where this target's devices come from is a seam (BE-0236): the provider `acquire` returns the
    # udid spec the lanes resolve against (the `--udid` flag verbatim for the default local provider,
    # a reserved serial / endpoint for a device cloud) plus what it already did to the device
    # (`provision`). Acquired before the `try` so its release runs even on a setup-time error below;
    # off the run/CI verdict path — no LLM, no assertion input.
    lease = acquire_device(eff, udid)
    try:
        # Web has no simctl udid: `--workers N` is N near-free BrowserContext lanes (BE-0054); for
        # idb, `--udid` is a concrete comma list capped to the pool size. (The "booted" default is
        # unused on web.) How a device handle resolves is the platform's, behind the Environment seam
        # (BE-0256): Android via adb, the iOS family via simctl — no `actuator == "adb"` branch here.
        udids, workers = _resolve_lanes(
            actuator,
            lease.udid_spec,
            workers,
            environment_for(actuator, lease.udid_spec).resolve_device,
        )
        _apply_dismiss_alerts(scenarios, dismiss_alerts)
        on_blocked_for = _alert_guard_factory(scenarios, eff, alert_instruction)
        # Network collection resolves `--network/--no-network` over the target's `network` config,
        # then on (BE-0177); the resolved bool baked into mocks and the plan drives collection and
        # `request` waits.
        network = _resolve_network(network, eff.run_defaults.network)
        _apply_mocks(scenarios, network)
        baselines_dir, schemas_dir, gc = _resolve_evidence_dirs(
            baselines, schemas, goldens, eff, files[0]
        )
        plan = _RunPlan(
            eff=eff,
            config_source=config_source,
            target_name=target_name,
            scenarios=scenarios,
            description=description,
            source_name=source_name,
            engines=engines,
            actuator=actuator,
            backends=backends,
            udids=udids,
            udid_spec=lease.udid_spec,
            workers=workers,
            provision=lease.provision,
            on_blocked_for=on_blocked_for,
            baselines_dir=baselines_dir,
            schemas_dir=schemas_dir,
            golden_context=gc,
            secret_bindings=secret_bindings,
            secret_values=secret_values,
            run_id=new_run_id(),
            runs_dir=Path(runs_dir),
            network=network,
            log_predicate=log_predicate,
            log_subsystem=log_subsystem,
            progress=progress,
            zip_run=zip_run,
            evidence_store=evidence_store,
            upload_exec=upload_exec,
        )
        # Install the usage/cost ledger before dispatch (BE-0196). Reporting only — emission is
        # best-effort and off the deterministic verdict path (prime directive 1). Per-scenario
        # `command` / `scenario` attribution is bound at the alert guard (`_alert_guard_factory`),
        # which fires inside the runner's worker threads, so it reaches the worker under `--workers N`.
        _usage_ledger.configure_from_ai_config(eff.ai)
        # Snapshot AI usage before dispatch — the alert guard is the only thing that can spend tokens,
        # and it fires during dispatch, so `_finish` reports exactly what this run used.
        before = _usage.snapshot()
        results, manifest = _dispatch(plan)
        _finish(plan, results, manifest, before)
    finally:
        # Hand the device back to its provider (a no-op for the local one), even on failure so a
        # reserved cloud device is never leaked (BE-0236). Warn-only, never propagate: a provider's
        # teardown failure must not flip or mask the machine-only verdict, the same rule the
        # post-verdict zip/upload steps honor — a leaked device is loud on stderr, not a crash.
        try:
            lease.release()
        except Exception as exc:
            typer.echo(
                f"warning: device release failed ({exc}); a reserved device may be leaked", err=True
            )


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(run)

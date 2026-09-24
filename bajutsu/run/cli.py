"""`bajutsu run` — execute a scenario deterministically (the Tier-2 CI gate)."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from bajutsu.common.doctor import Score

import typer
from pydantic import ValidationError

from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    LoadedConfig,
    _effective_for,
    _load_config_with_source,
    _log_subsystem_default,
    _resolve_browser,
    _select_actuator_or_exit,
    _start_launch_server_or_exit,
    _with_headed,
    resolve_system_alert_handling_flag,
)
from bajutsu.common.assertions import GoldenContext
from bajutsu.common.backends import (
    default_available,
    select_actuator,
    select_actuator_for_scenario,
)
from bajutsu.common.cancellation import CancelSource, graceful_sigterm
from bajutsu.common.config import WEB_ENGINES, Effective, IosConfig
from bajutsu.common.deprecations import warn_once
from bajutsu.common.devices import errors as device_errors
from bajutsu.common.github import actions as github_actions
from bajutsu.common.orchestrator import DEFAULT_ALERT_POLL_INTERVAL, AlertGuardConfig, RunResult
from bajutsu.common.orchestrator.types import ResolvedAlertRule
from bajutsu.common.platform_lifecycle import ProvisionProfile, environment_for
from bajutsu.common.report import ScenarioPlanSource
from bajutsu.common.report.archive import archive_run_dir
from bajutsu.common.report.manifest import MAX_LABEL_LENGTH, _run_backend
from bajutsu.common.run_meta.files import DEFAULT_RUNS_DIR
from bajutsu.common.run_meta.id import new_run_id
from bajutsu.common.runner import device_pool, run_all, run_and_report, run_matrix_and_report
from bajutsu.common.runner.build import BuildError, build_if_missing
from bajutsu.common.runner.device_provider import DeviceLease, acquire_device
from bajutsu.common.runner.pipeline import with_lifecycle_phases
from bajutsu.common.runner.types import AlertGuardFor, LeaseFn, TargetPool
from bajutsu.common.scenario import (
    ComponentResolver,
    RawScenario,
    Scenario,
    Step,
    SystemAlertHandling,
    SystemAlertHandlingField,
    SystemAlertRule,
    _scenarios_declaring_targets,
    apply_setups,
    contained_ref,
    declared_name,
    dump_mocks,
    expand_components,
    expand_data,
    load_scenario_file,
    parse_yaml_named,
    read_csv,
    scenario_sources,
    select_scenarios,
)
from bajutsu.common.scenario.system_alerts import (
    UncoveredSystemAlertLocale,
    alert_surfaces,
    covered_languages,
    system_alert_shapes,
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
    if config_value:
        return Path(config_value)
    return scenario_file.parent / default_name


def _declared_targets_in(path: Path) -> list[str]:
    """Every target the scenarios in *path* declare, in declared order, read before expansion.

    `run` has to know whether a file is self-declaring (BE-0428) before it can resolve `--target`
    into the `Effective` the ordinary load needs, so this reads the file on its own rather than
    waiting for `_load_scenarios`. Parse and schema errors exit 2 here the same way they would
    there; the file is parsed again by the ordinary load, which is cheap next to a device run.
    """
    try:
        scenarios = load_scenario_file(path.read_text(encoding="utf-8")).scenarios
    except (OSError, ValueError) as e:
        typer.echo(f"scenario の読み込みに失敗 ({path}): {e}")
        raise typer.Exit(2) from None
    seen: dict[str, None] = {}
    for s in scenarios:
        seen.update(dict.fromkeys(s.targets))
    return list(seen)


def _resolve_primary_target(target_name: str, scenario: list[str]) -> str:
    """The target `run` resolves its config, pool, and legacy scenarios against.

    `--target` when given, unchanged. Omitted, the run is driven entirely by self-declaring
    scenarios (BE-0428): at least one `--scenario` is required, since the directory-glob shorthand
    belongs to one target, every named file must declare its own `targets`, and the first file's
    first declared name becomes the primary — the one whose pool, secrets, and evidence dirs stand
    for the run, with every other declared target resolved alongside it.
    """
    if target_name:
        return target_name
    if not scenario:
        typer.echo(
            "--target is required unless every --scenario file declares its own targets; "
            "pass --target <name>, or name self-declaring files with --scenario"
        )
        raise typer.Exit(2)
    primary = ""
    for raw in scenario:
        path = Path(raw)
        if not path.exists():
            typer.echo(f"scenario not found: {path}")
            raise typer.Exit(2)
        declared = _declared_targets_in(path)
        if not declared:
            typer.echo(
                f"--target is required: {path} declares no targets of its own, so there is "
                "nothing to resolve it from — pass --target <name>, or give that file a "
                "`targets:` field"
            )
            raise typer.Exit(2)
        primary = primary or declared[0]
    return primary


def _reject_self_declaring_in_dir(files: list[Path], target_name: str) -> None:
    """Refuse a self-declaring scenario reached through the target's scenarios dir (BE-0428).

    The directory glob is `run`'s only whole-suite shorthand and it belongs to one target, so a
    file declaring its own `targets` can only ever run through an explicit `--scenario`. Rejecting
    at discovery keeps the two bad outcomes off the table regardless of where the file sits: a
    mismatching name would fail every other scenario in the batch alongside it, and a matching one
    would silently launch every other target that file declares.
    """
    for path in files:
        if _declared_targets_in(path):
            typer.echo(
                f"{path} declares its own targets, so it cannot run through target "
                f"'{target_name}'s scenarios dir — name it with --scenario instead"
            )
            raise typer.Exit(2)


def _scenario_files(
    eff: Effective, scenario: list[str], target_name: str
) -> tuple[list[Path], bool]:
    """The scenario files `run` should load.

    The `--scenario` files when given (an explicit override — repeat the flag to run several in one
    process, sharing one warm runner), else every `*.yaml` in the target's configured `scenarios`
    dir. Returns `(files, single)` where `single` flags the lone-file override (so the report can
    carry that file's name/description); an explicit list of two or more is not `single`.
    """
    if scenario:
        paths = [Path(s) for s in scenario]
        for path in paths:
            if not path.exists():
                typer.echo(f"scenario not found: {path}")
                raise typer.Exit(2)
        return paths, len(paths) == 1
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
    _reject_self_declaring_in_dir(files, target_name)
    return files, False


def _expand_file(
    path: Path, eff: Effective, root: Path
) -> tuple[list[Scenario], str | None, dict[str, ScenarioPlanSource]]:
    """Load one scenario file and expand its setup/component/data refs.

    Each ref is resolved relative to THIS file's directory, so a multi-file dir run keeps every
    file's refs local. Component and data refs are confined to *root* (the suite dir, or the file's
    own dir for a single-file run), so a scenario cannot read outside its suite (BE-0174). Returns
    the expanded scenarios, the file-level description, and — keyed by each *expanded* scenario's
    declared name — its report plan source: the scenario's own verbatim YAML as authored
    in *path*, and its steps' original line numbers there. A scenario whose setup/component
    expansion changed its step count keeps the verbatim text but drops its line numbers, since a
    wrong line would be worse than none. A data-driven scenario (`data`/`dataFile`) drops the
    verbatim text entirely: `expand_data` substitutes `${row.*}` per row without changing the step
    count, so the guard above would not catch it, and every row would otherwise share one
    unsubstituted template — showing none of them what actually ran.
    """
    text = path.read_text(encoding="utf-8")
    scenario_file = load_scenario_file(text)
    scenarios = scenario_file.scenarios
    raw_sources = scenario_sources(text)
    pre_step_counts = {s.name: len(s.steps) for s in scenarios}

    def _plan_source(s: Scenario, raw: RawScenario) -> ScenarioPlanSource:
        if s.data is not None or s.data_file is not None:
            return ScenarioPlanSource(file_name=path.name, text=None, step_lines=[])
        return ScenarioPlanSource(file_name=path.name, text=raw.text, step_lines=raw.step_lines)

    plan_by_name = {
        s.name: _plan_source(s, raw) for s, raw in zip(scenarios, raw_sources, strict=True)
    }
    # Refs (setup/use/data) resolve relative to this scenario file's own directory.
    base_dir = path.parent

    def _setup_steps(ref: str) -> list[Step]:
        """The prelude's own steps, its `use` steps already expanded in the prelude's own scope.

        A prelude is a scenario-file-shaped document and carries a `components:` map of its own, so
        its bare refs must resolve there — never against whichever scenario file happened to name it
        as `setup` (BE-0422). Expanding before `apply_setups` splices is what guarantees that: no
        unexpanded `use` ever crosses from a prelude into the scenario including it.

        The prelude path itself stays inside the suite root (BE-0174), the same as every other ref
        this function resolves — a scenario file is untrusted input under `serve`, so `setup` gets no
        exemption from the containment every `use`/`dataFile` ref already has.
        """
        prelude = contained_ref(root, base_dir, ref)
        prelude_file = parse_yaml_named(prelude, load_scenario_file)
        scenario = prelude_file.scenarios[0]
        expand_components(
            [scenario],
            ComponentResolver(
                prelude_file.components, root=root, base=prelude.parent, source=prelude
            ),
        )
        return scenario.steps

    try:
        apply_setups(scenarios, eff.setup, _setup_steps)
    except (OSError, ValueError, IndexError) as e:
        typer.echo(f"setup の読み込みに失敗: {e}")
        raise typer.Exit(2) from None
    try:
        expand_components(
            scenarios,
            ComponentResolver(scenario_file.components, root=root, base=base_dir, source=path),
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
    plan_sources: dict[str, ScenarioPlanSource] = {}
    for s in scenarios:
        s.set_source_stem(path.stem)
        base_name = declared_name(s.name)
        plan = plan_by_name.get(base_name)
        if plan is None:
            # `declared_name` strips a `[row N]`-shaped suffix unconditionally, so a scenario
            # authored with a literal name that happens to match it (not one `expand_data` added)
            # strips to a name `plan_by_name` never had. That is a valid scenario file, not a bug
            # here, so this scenario simply gets no recovered plan rather than crashing the run.
            continue
        # `apply_setups`/`expand_components` mutate `Scenario.steps` in place, so a changed count
        # against the pre-expansion snapshot means this scenario's line numbers no longer line up
        # with its executed steps (data-row expansion never changes the count, so a row keeps them).
        if pre_step_counts.get(base_name) != len(s.steps):
            plan = ScenarioPlanSource(file_name=plan.file_name, text=plan.text, step_lines=[])
        plan_sources[base_name] = plan
    return scenarios, scenario_file.description, plan_sources


def _resolve_config_and_engines(
    config: str,
    target_name: str,
    *,
    offline: bool,
    require_pinned: bool,
    headed: bool | None,
    browser: str,
    browsers: str,
) -> tuple[LoadedConfig, Effective, dict[str, str] | None, list[str]]:
    """Resolve the effective config (building a Git-sourced app on demand) and the engine list.

    Applies `--headed` and `--browser`, then parses `--browsers` into the cross-browser matrix axis
    (BE-0076). Returns the loaded config itself — so a multi-target scenario resolves every other
    name it declares against the same materialized file (BE-0428) — the resolved config for
    *target_name*, its Git source provenance (None for a local config), and
    the requested engines exactly as `--browsers` gave them: empty when `--browsers` is absent, a
    single entry — already collapsed onto `eff.browser`, the single-engine path — for one engine, or
    every listed engine for several. Only `len(...) > 1` takes the matrix path downstream.
    """
    loaded = _load_config_with_source(config, offline=offline, require_pinned=require_pinned)
    eff = _effective_for(loaded, target_name)
    config_source, checkout_root = loaded.source, loaded.root
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
    return loaded, eff, config_source, engines


def _resolve_secrets(effs: Iterable[Effective]) -> tuple[dict[str, str], list[str]]:
    """Resolve declared secrets from the environment into ${secrets.X} bindings and mask values.

    Only secrets actually present in the environment are bound. The literal values are collected so
    evidence and run-level artifacts can mask them (the scenario definition keeps the token, never
    the value). Every declared target's own `secrets` names are read (BE-0428), so a `${secrets.X}`
    a step routed to a non-primary target uses still binds — `X` may be declared only on that
    target's own config, not the primary's.
    """
    names = dict.fromkeys(n for eff in effs for n in eff.secrets)  # ordered-unique across targets
    bindings = {f"secrets.{n}": os.environ[n] for n in names if n in os.environ}
    return bindings, list(bindings.values())


def _load_scenarios(
    eff: Effective, scenario: list[str], target_name: str
) -> tuple[list[Scenario], str | None, str, list[Path], dict[str, ScenarioPlanSource]]:
    """Load and fully expand the run's scenarios: the `--scenario` files, or the target's dir.

    Each file's setup/component/data refs resolve relative to its own directory, then the expanded
    scenarios concatenate into one run. Returns the scenarios, the single-file description (None for
    a multi-file or directory run), the report's source label, the source files, and every
    scenario's report plan source — see `_expand_file`.
    """
    files, single = _scenario_files(eff, scenario, target_name)
    # The containment root for refs (BE-0174): the single file's own directory for a lone `--scenario`
    # override; the common parent of an explicit multi-file `--scenario` list; else the configured
    # scenarios dir for a whole-suite run.
    if single:
        root = files[0].parent
    elif scenario:
        root = Path(os.path.commonpath([str(f.parent) for f in files]))
    else:
        root = Path(eff.evidence_dirs.scenarios or files[0].parent)
    scenarios: list[Scenario] = []
    description: str | None = None
    plan_sources: dict[str, ScenarioPlanSource] = {}
    # Two files can declare the same scenario name — nothing upstream enforces uniqueness across
    # files, only within one (`_expand_file`'s own dict). `plan_sources` is keyed by that name, so a
    # collision here cannot tell the two scenarios' panels apart; the ambiguous name is dropped
    # rather than let one file's source silently attach to the other file's scenario.
    ambiguous: set[str] = set()
    for path in files:
        expanded, file_desc, file_plan_sources = _expand_file(path, eff, root)
        scenarios.extend(expanded)
        ambiguous.update(file_plan_sources.keys() & plan_sources.keys())
        plan_sources.update(file_plan_sources)
        if single:
            description = file_desc
    for name in ambiguous:
        del plan_sources[name]
    # The report's source label: the single file's name, else the root dir's name.
    source_name = files[0].name if single else root.name
    return scenarios, description, source_name, files, plan_sources


def _check_target_membership(
    scenarios: list[Scenario], target_name: str, *, explicit: bool
) -> None:
    """Refuse an explicit `--target` that no longer names one of a scenario's own targets.

    A self-declaring scenario resolves its own targets, so `--target` is redundant beside it — but
    ignoring it would let a stale flag, left over from editing the scenario, silently select a
    target the file no longer expects. Checked for membership instead: matching any one declared
    name is fine, naming none is an error (BE-0428). A primary this command derived itself, from
    the scenarios rather than from a flag, is not checked — a batch's later file may legitimately
    declare a disjoint target set from the first file's.
    """
    if not explicit:
        return
    for s in scenarios:
        if s.targets and target_name not in s.targets:
            typer.echo(
                f"--target '{target_name}' is not one of scenario '{s.name}'s declared targets "
                f"{s.targets} — drop --target, or name one of them"
            )
            raise typer.Exit(2)


def _reject_legacy_without_target(
    scenarios: list[Scenario], target_name: str, *, explicit: bool
) -> None:
    """Refuse a legacy (targetless) scenario in a batch that supplied no `--target` (BE-0428).

    A scenario with no `targets` of its own has nothing to resolve a target from but the
    invocation's single `--target`, exactly as today. `_resolve_primary_target` already catches the
    files named on the command line; this catches the ones a `setup`/`use` expansion or a data-row
    fan-out produced after that pre-pass, so no scenario ever runs against a target it never named.
    """
    if explicit:
        return
    orphans = [s.name for s in scenarios if not s.targets]
    if orphans:
        typer.echo(
            f"--target is required: scenario(s) {', '.join(orphans)} declare no targets of their "
            f"own, so '{target_name}' would be resolved from another file — pass --target, or "
            "give them a `targets:` field"
        )
        raise typer.Exit(2)


def _resolve_target_effs(
    loaded: LoadedConfig,
    scenarios: list[Scenario],
    primary: str,
    primary_eff: Effective,
    *,
    headed: bool | None = None,
    browser: str = "",
) -> dict[str, Effective]:
    """One already-rebased `Effective` per target any scenario in this run declares (BE-0428).

    Resolved through the same chain the primary target goes through, so a second target's relative
    `appPath` / `baselines` / `goldens` resolve against the config file's own directory rather than
    the caller's working directory (BE-0242). An unknown name exits 2 here, before any device work,
    the same way an unknown `--target` already does. The primary's own entry reuses the
    already-resolved `Effective` so the run's `--headed` / `--browser` overrides are not applied
    twice; every other target gets them applied here, so a run's single web target still sees them
    when it is not the primary — `_reject_web_flags_across_targets` is what refuses them outright
    once a run declares two web targets, since which one they would mean has no answer there.
    """
    effs = {primary: primary_eff}
    for s in scenarios:
        for name in s.targets:
            if name not in effs:
                effs[name] = _resolve_browser(
                    _with_headed(_effective_for(loaded, name), headed), browser
                )
    return effs


def _reject_web_flags_across_targets(
    target_effs: Mapping[str, Effective], *, headed: bool | None, browser: str, browsers: str
) -> None:
    """Refuse the web engine flags when the run declares more than one web-platform target.

    `--headed` / `--browser` / `--browsers` each apply to a run's single web target, and this item
    leaves that axis untouched: which of two declared web targets they mean has no answer, so a
    scenario naming both rejects them outright rather than having one silently picked (BE-0428).
    """
    web = sorted(name for name, eff in target_effs.items() if eff.platform == "web")
    if len(web) < 2:
        return
    if headed is not None or browser or browsers:
        typer.echo(
            "--headed / --browser / --browsers apply to a run's single web target, but this run "
            f"declares {len(web)}: {', '.join(web)} — set each target's own `headless` / `browser` "
            "config instead"
        )
        raise typer.Exit(2)


def _reject_cross_browser_matrix_with_targets(
    scenarios: list[Scenario], engines: list[str]
) -> None:
    """Refuse `--browsers <2 engines>` on a scenario declaring `targets` (BE-0428).

    The matrix path (`run_matrix_and_report` / `_dispatch_matrix`) runs one full pass per engine
    against one pool and carries no per-target map, so it cannot launch a scenario's other declared
    targets at all. Without this check, `run_all` would raise a bare `ValueError` well after every
    device was already leased, instead of the clean exit 2 every other multi-target refusal gives.
    Extending the matrix axis to a multi-target run is separate work.
    """
    if len(engines) < 2:
        return
    affected = _scenarios_declaring_targets(scenarios)
    if affected:
        typer.echo(
            "--browsers cannot fan out a scenario declaring targets: (BE-0428); "
            f"affected scenario(s): {', '.join(affected)}"
        )
        raise typer.Exit(2)


def _reject_bad_target_config_hooks(
    target_effs: Mapping[str, Effective], scenarios: list[Scenario], primary: str
) -> None:
    """Fail fast, with a clean CLI message, on a `target` mismatch folded in from config (BE-0428).

    `with_lifecycle_phases` re-validates its own folded result already — but only `run_all` calls
    it, deep inside a path this command's own `try` doesn't catch (its `finally` only releases the
    device lease). A `targets.<name>.before`/`after` hook step carrying a `target` that disagrees
    with the scenario it folds into would otherwise reach the operator as a raw traceback instead
    of the clean exit 2 every other scenario-loading error gets. Calling it here again, and
    discarding the result, is redundant with `run_all`'s own call on the same inputs — cheap, and
    it means passing this check guarantees `run_all` will too.
    """
    try:
        with_lifecycle_phases(target_effs[primary], scenarios, target_effs)
    except ValueError as e:
        typer.echo(f"config-level before/after hook: {e}")
        raise typer.Exit(2) from None


def _filter_scenarios(
    scenarios: list[Scenario],
    tag: str,
    exclude: str,
    erase: bool | None,
    target_erase: bool,
    ios_tipkit_handling: bool | None = None,
    target_ios_tipkit_handling: bool = False,
) -> list[Scenario]:
    """Apply `--tag`/`--exclude` selection and resolve each scenario's `preconditions.erase`.

    Selection runs over the combined set; an empty result is a usage error (exit 2). Erase resolves
    most-specific-wins (BE-0177): `--erase` / `--no-erase` overrides every scenario, else a scenario's
    own explicit value, else *target_erase* (the target config default, already the built-in off when
    unset). Leaves every scenario with a concrete bool, so downstream never sees the unset `None`.
    `iosTipKitHandling` resolves by the same precedence, and likewise lands as a concrete bool.
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
        if ios_tipkit_handling is not None:
            s.ios_tip_kit_handling = ios_tipkit_handling
        elif s.ios_tip_kit_handling is None:
            s.ios_tip_kit_handling = target_ios_tipkit_handling
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


def _apply_system_alert_handling(
    scenarios: list[Scenario], system_alert_handling: bool | None
) -> None:
    """Apply the `--system-alert-handling` / `--no-system-alert-handling` override to every scenario.

    Turning the guard on preserves whatever policy the scenario already declared and re-enables one
    that had switched itself off; turning it off replaces the whole field with `False`, which is what
    off *is* now the boolean carries on and off (BE-0401) — an off guard reads no policy. A no-op
    when the flag is unset (each scenario's own `systemAlertHandling`, default on, decides). Mirrors
    the `--erase` override.
    """
    if system_alert_handling is None:
        return
    for s in scenarios:
        if not system_alert_handling:
            s.system_alert_handling = False
            continue
        prev = s.system_alert_handling
        s.system_alert_handling = (
            prev if isinstance(prev, SystemAlertHandling) else (SystemAlertHandling())
        )


def _resolve_rules(rules: list[SystemAlertRule], locale: str) -> list[ResolvedAlertRule]:
    """Each rule's prompt resolved to one entry per rendering it has under `locale`.

    A prompt renders as more than one shape when the operating system varies its buttons by context
    or by version, so one scenario rule can resolve to several matchable rules (BE-0406). Each
    carries its prompt's `AlertSurfaces` record, which is what decides later whether the in-tree
    paths may arm on it and whether it may be pushed to the interruption monitor.

    Raises:
        UncoveredSystemAlertLocale: a rule names a prompt the label table has no entry for under
            `locale`'s language — the same fail-loudly choice `handleSystemAlert`'s own
            `prompt`/`choice` resolution makes, rather than guessing at a label. Re-raised naming
            *this* surface, since the lookup's own message is phrased for that step.
    """
    resolved: list[ResolvedAlertRule] = []
    for rule in rules:
        try:
            shapes = system_alert_shapes(rule.prompt, rule.choice, locale)
        except UncoveredSystemAlertLocale as exc:
            # The lookup's message names `handleSystemAlert` and offers its `sel.label` remedy —
            # neither of which a scenario reaching here need have written. Re-scope it to the guard
            # and to the two remedies that remain for it, so a loud failure names the surface that
            # actually failed.
            covered = ", ".join(covered_languages(rule.prompt))
            raise UncoveredSystemAlertLocale(
                f"systemAlertHandling.rules prompt: {rule.prompt} has no known button labels for "
                f"locale {locale!r}; covered: {covered}. Add the language to "
                "bajutsu/common/scenario/system_alerts.py, or pin a locale the table covers"
            ) from exc
        surfaces = alert_surfaces(rule.prompt)
        resolved.extend(
            ResolvedAlertRule(
                identifying_labels=shape.identifying_labels,
                tap_label=shape.tap_label,
                excluded_labels=shape.excluded_labels,
                native=surfaces["native"],
                in_tree=surfaces["in_tree"],
            )
            for shape in shapes
        )
    return resolved


def _flag_alert_policy(poll_interval: float | None) -> SystemAlertHandling | None:
    """The command-line layer of `systemAlertHandling`, or None when no flag declares one.

    Only `pollInterval` has a flag. `rules` has none: an entry pairs a prompt with a choice, which
    one flag value cannot carry legibly, so a per-prompt declaration stays a scenario-file and
    target-config one (BE-0401). `--alert-labels` went with `labels` itself (BE-0406), and
    `visionInstruction` has had none since BE-0402 removed `run`'s vision fallback.

    Raises:
        typer.Exit: a flag's value does not satisfy the schema, reported as a CLI error rather than a
            validation traceback.
    """
    fields: dict[str, Any] = {}
    if poll_interval is not None:
        fields["pollInterval"] = poll_interval
    if not fields:
        return None
    try:
        return SystemAlertHandling.model_validate(fields)
    except ValidationError as exc:
        typer.echo(f"invalid alert-guard flag: {exc}")
        raise typer.Exit(2) from exc


def _warn_target_rules_reach(
    s: Scenario,
    scenario_rules: list[SystemAlertRule],
    target_rules: list[SystemAlertRule],
) -> None:
    """Notice each target rule that will answer a prompt inside a scenario answering for itself.

    Composition restores a case BE-0382 removed on purpose. The behavior is correct under the
    specificity ladder — the layers concatenate, innermost first — but BE-0382's objection was that
    it is *silent*: a project-wide edit changes a scenario that already declares for itself. The
    notice keeps the composition and removes the silence. Only a rule the scenario does not already
    rule on is named, since its own rule shadows the target's for that prompt.

    "Answering for itself" reads the scenario's own rules and nothing else: `rules` is the only
    declaration left (BE-0406) and no flag can carry one, so the scenario is the only layer inside
    the target's that a declaration can come from.

    Keyed on the scenario *and* the prompt, not the prompt alone: `warn_once` dedupes for the whole
    process, so a prompt-only code would warn for the first affected scenario of a run and pass over
    the rest in the very silence this removes.
    """
    if not target_rules or not scenario_rules:
        return
    ruled = {r.prompt for r in scenario_rules}
    for rule in target_rules:
        if rule.prompt in ruled:
            continue
        warn_once(
            f"systemAlertHandling.targetRule.{s.name}.{rule.prompt}",
            f"scenario {s.name!r} declares its own system-alert rules, and the target config's "
            f"rule for the {rule.prompt} prompt still answers it ({rule.choice}); "
            "write the scenario's own rule for that prompt to override it.",
        )


def _policy_of(value: SystemAlertHandlingField) -> SystemAlertHandling | None:
    """The policy a layer declares, or None when it declares none (absent) or switches the guard off.

    `False` and an absent key are both "no policy here" to every caller that reads declarations; only
    the enabled check below distinguishes them.
    """
    return value if isinstance(value, SystemAlertHandling) else None


def _reject_vision_instruction(
    scenarios: list[Scenario], target_policy: SystemAlertHandling | None
) -> None:
    """Stop the whole run when a layer supplies a `visionInstruction` (BE-0402).

    The key steers only the AI-vision fallback, which `run` no longer has, so acting on it is
    impossible and ignoring it is worse than failing: a scenario that wrote `visionInstruction: "tap
    Allow"` to *grant* a permission would silently fall through to the built-in dismissive labels and
    deny it instead — the silent wrong answer BE-0382 spent its Motivation ruling out for the same
    field. The key itself now reaches no command: `run` refuses it here, and `record` / `crawl` read
    the free-text form only from their own `--alert-vision-instruction` flag, never from a scenario or
    a target config. It stays in the schema so a file carrying it gets this message rather than
    Pydantic's generic "extra fields not permitted" — the same reason BE-0401 kept `instruction`
    reachable long enough to name its replacement. Two layers can carry it — a scenario and the
    target config's `run_defaults`; the command line cannot, since `run` retired the flag that set it.

    Checked eagerly over every scenario, before the per-scenario closure is ever returned. That
    closure runs inside the run loop, in a worker, so a check placed there would fire on scenario N
    with scenarios 1…N-1 already executed — partway through a run. This deliberately differs from
    `resolved_locale`, which does raise from inside the closure and is caught as one scenario's
    failure: an uncovered locale is a condition of the run, an unusable `visionInstruction` is an
    authoring mistake in the file and is detectable without a device, so a suite is rejected or
    accepted whole.

    Raises:
        typer.Exit: any layer supplies the key, reported as a CLI error naming where it came from.
    """
    named = [
        f"scenario {s.name!r}"
        for s in scenarios
        if (policy := _policy_of(s.system_alert_handling)) is not None and policy.vision_instruction
    ]
    if target_policy is not None and target_policy.vision_instruction:
        named.append("the target config's run_defaults")
    if not named:
        return
    typer.echo(
        f"systemAlertHandling.visionInstruction is not supported by `run` ({', '.join(named)}): "
        "it steers only the AI-vision fallback, which `run` no longer has (BE-0402). Answer the "
        "prompt you expect with `rules: [{ prompt: ..., choice: ... }]` instead. "
        "`record` and `crawl` still read it."
    )
    raise typer.Exit(2)


def _alert_guard_factory(
    scenarios: list[Scenario], eff: Effective, flag_policy: SystemAlertHandling | None
) -> AlertGuardFor | None:
    """Build a per-scenario alert-guard factory, or None when no scenario wants a guard.

    Each scenario gets its own `AlertGuardConfig`, which since BE-0402 is deterministic throughout:
    the native path (BE-0315, reusing BE-0316's `handle_system_alert`) and the in-tree dismiss, and
    nothing else. `run` reaches no model here under any flag, so no AI credential is consulted and
    none is needed.

    A setting reaches the run from three layers — the scenario, *flag_policy* (the command line), and
    the target config (BE-0177) — composed by the key's type (BE-0401): `rules` concatenates
    innermost layer first, so both layers' entries stay reachable, and `pollInterval` takes the
    innermost layer that supplies one. `rules` reaches only two of the three, since no flag can carry
    a prompt paired with a choice legibly.

    Raises:
        typer.Exit: a layer supplies a `visionInstruction`, which `run` can no longer act on.
    """

    # A scenario's guard is on when its own `systemAlertHandling` says so, else the target config's,
    # else the built-in on (BE-0177). The `--system-alert-handling` flag is already baked onto the
    # scenario by `_apply_system_alert_handling`, so it needs no separate check here.
    def _enabled(s: Scenario) -> bool:
        if s.system_alert_handling is not None:
            return s.system_alert_handling is not False
        if eff.run_defaults.system_alert_handling is not None:
            return eff.run_defaults.system_alert_handling is not False
        return True

    if not any(_enabled(s) for s in scenarios):
        return None

    target_policy = _policy_of(eff.run_defaults.system_alert_handling)
    _reject_vision_instruction(scenarios, target_policy)

    def _guard_for(s: Scenario) -> AlertGuardConfig | None:
        if not _enabled(s):
            return None
        # Innermost layer first, so a scalar's precedence and a list's concatenation are the same
        # walk over the same sequence.
        scenario_policy = _policy_of(s.system_alert_handling)
        layers = [p for p in (scenario_policy, flag_policy, target_policy) if p is not None]
        poll_interval = next(
            (layer.poll_interval for layer in layers if layer.poll_interval is not None),
            DEFAULT_ALERT_POLL_INTERVAL,
        )

        # The scenario's own rules ahead of the target's: matching returns on the first rule whose
        # prompt it identifies, so a rule for the same prompt in both layers is an override, not the
        # parse-time error a duplicate within one list is. Both layers stay in effect — the target's
        # rules answer every prompt the scenario ruled on none of — which is the suppression BE-0382
        # defined and BE-0401 reversed, under the notice below. Resolved against this scenario's own
        # locale — the same value the run pins the Simulator's system language to — so a rule's labels
        # are the ones actually on screen; an uncovered language raises here, before this scenario's
        # device work (caught by the runner as a scenario failure).
        scenario_rules = scenario_policy.rules if scenario_policy else []
        target_rules = target_policy.rules if target_policy else []
        # The notice reads the scenario's own declaration, not the concatenation below: that carries
        # the target's *own* rules, which would make a scenario that declares nothing look like one
        # answering for itself.
        _warn_target_rules_reach(s, scenario_rules, target_rules)
        locale = s.preconditions.resolved_locale(eff.locale)
        rules = _resolve_rules([*scenario_rules, *target_rules], locale)

        return AlertGuardConfig(rules=rules, poll_interval=poll_interval)

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


def _visual_asserting_scenarios(scenarios: list[Scenario]) -> set[int]:
    """`id()`s of the scenarios whose verdict reads a screenshot, from `expect` or from any step.

    Keyed by object identity, not `.name`: nothing enforces unique scenario names across a
    multi-file run (`_load_scenarios` just concatenates each file's own scenarios), and a
    name-keyed set would conflate two same-named scenarios that need different answers below.
    """
    visual = set()
    for s in scenarios:
        assertions = [*s.expect, *(a for step in s.steps for a in step.assert_ or [])]
        if any(a.visual is not None for a in assertions):
            visual.add(id(s))
    return visual


def _apply_touch_markers(
    scenarios: list[Scenario],
    enabled: bool,
    *,
    channel_available: Callable[[Scenario], bool],
    target_launch_env: Mapping[str, str] | None = None,
) -> None:
    """Ask BajutsuKit to draw a marker at each touch the app receives, via the launch env.

    Off unless asked for: the marker is drawn inside the app under test, so it belongs to a run
    someone is investigating rather than to every run. A scenario that already sets the variable
    keeps its own value, like the mocks above.

    A scenario whose verdict compares a screenshot additionally needs the in-app control channel
    (BE-0365), which the run loop uses to hide the markers for exactly that capture and restore
    them after. The markers persist until the next gesture by design, so the image a `visual`
    assertion reads would otherwise carry a circle and a trail its baseline does not; masking
    cannot rescue that, since the marker follows the gesture instead of occupying a fixed region.

    `channel_available` is the caller's own answer to whether a *given scenario* can carry the
    channel — the `xcuitest` actuator (a real Simulator process) with network collection on, since
    the channel rides the network collector and the app-side poll loop BajutsuKit runs only there,
    never under `fake` (nothing polls its collector) or a backend with no such collector at all. It
    takes a scenario rather than a run-level bool because the actuator itself is chosen per scenario
    once `--backend` resolves to more than one candidate (BE-0240, `select_actuator_for_scenario`):
    a run-level answer would arm the channel on a scenario that escalated to a backend whose
    collector deliberately carries none, failing it on a wait no app there will ever answer. The
    caller's own answer has to stay run-aware as well as per-scenario, since the collectors
    themselves are provisioned from the run-level actuator — see `_channel_available_for`.
    Where the channel cannot be carried, this reverts to the pre-BE-0365 behavior instead: that
    scenario's markers stay off, at the same per-scenario granularity
    ([`docs/evidence.md`](../../docs/evidence.md) — a relaunched process is unaffected by another
    scenario's own launch env either way).

    Where these partitions read a launch env they read `target_launch_env` merged with the
    scenario's own, the same merge `_hides_touch_markers` performs and the same order the launch
    itself merges them in — so a target that pins the marker key for every scenario is decided for
    here exactly as the app that launches sees it, not as if every scenario had pinned nothing.
    `target_launch_env` is the target's own `launchEnv` (`Effective.launch_env`); a caller that
    omits it sees only each scenario's own launch env, as before.

    Every internal decision here is keyed by scenario object identity, never by `.name`: nothing
    enforces unique scenario names across a multi-file run, and a name-keyed lookup would let two
    same-named scenarios that resolve to different actuators share one verdict on whether either
    can carry the channel — arming it on one that structurally cannot.

    Every outcome says so on stderr, since all six are silent in the evidence otherwise: where
    the channel is available, one of its two gates is a build setting bajutsu cannot see from
    here, so this says what an app that answers nothing will fail with; where it is unavailable or
    declined, the markers an investigator asked for simply do not appear — unless the merged env
    already pins `BAJUTSU_TOUCH_MARKERS` to `"1"` (the scenario's own pin, which `setdefault` never
    overrides, or its target's, which the write loop below skips outright since there is nothing
    of the scenario's own for `setdefault` to leave alone). That one keeps drawing markers nothing
    here can hide, *unless* the merged env also pins `BAJUTSU_CONTROL_CHANNEL` to `"1"` on a run
    that cannot carry it — the run loop reads both keys off that same merged env with no memory of
    this function's own prediction, so that combination fails the scenario loudly instead. The
    sixth is the mirror of that one: a scenario with no `visual` verdict whose merged env pins
    `BAJUTSU_CONTROL_CHANNEL` to `"1"` *and leaves the marker key unset* gets none from here, since
    writing it is what would complete the pair and hand that same failure to a scenario this
    function was never deciding for. One whose merged env already pins both keys is outside this
    guard — the pair already exists, nothing here made it, and there is no note.
    """
    if not enabled:
        return
    target_env = target_launch_env or {}

    def _env(s: Scenario) -> Mapping[str, str]:
        # Same merge and order the launch itself performs, so a target-level pin reads here
        # exactly as the app that launches sees it, not as a scenario that pinned nothing.
        return {**target_env, **s.preconditions.launch_env}

    visual_scenarios = _visual_asserting_scenarios(scenarios)
    # A scenario (or its target) that pinned the marker value to "0" draws no markers at all, so it
    # needs neither the channel nor the note below — arming either for it would start the app-side
    # poll timer for a scenario that opted out of the very thing the channel exists to correct
    # (golden_xcuitest.yaml pins this on the scenario itself). This is also what the write loop at
    # the bottom skips: `setdefault` is a no-op once the scenario's own dict already carries "0",
    # but a target-only "0" leaves that dict empty, so the loop has to check the merged value
    # itself rather than lean on `setdefault`'s own idempotence.
    merged_off = {id(s) for s in scenarios if _env(s).get("BAJUTSU_TOUCH_MARKERS", "1") != "1"}
    # Every partition below is keyed by `id(scenario)`, for the same reason
    # `_visual_asserting_scenarios` is: two scenarios can share a `.name` across a multi-file run.
    wants_markers = [s for s in scenarios if id(s) in visual_scenarios and id(s) not in merged_off]
    wants_marker_ids = {id(s) for s in wants_markers}
    # A scenario (or its target) can decline the channel the same way, by pinning
    # BAJUTSU_CONTROL_CHANNEL to anything but "1". Drawing markers with no channel to hide them
    # would fail its `visual` comparison silently and for a reason that has nothing to do with the
    # app — the one failure mode this channel exists to rule out — so a decline here falls back the
    # same way an unavailable channel does: no markers at all, announced below, rather than markers
    # arming a channel bajutsu was told not to use.
    declines_channel = {
        id(s) for s in wants_markers if _env(s).get("BAJUTSU_CONTROL_CHANNEL", "1") != "1"
    }
    needs_channel = [s for s in wants_markers if id(s) not in declines_channel]
    armed = {id(s) for s in needs_channel if channel_available(s)}
    # Both partitions can be non-empty in one run: availability follows the actuator each scenario
    # resolved to, so a `--backend ios,web` run can arm one scenario and skip the next.
    channel_less = {id(s) for s in needs_channel if id(s) not in armed}
    # `setdefault` below never overrides a key a scenario already set, so a scenario (or target)
    # that pinned `BAJUTSU_TOUCH_MARKERS: "1"` keeps drawing markers regardless of what this
    # function decides. What that actually leads to still depends on `BAJUTSU_CONTROL_CHANNEL`,
    # which `_hides_touch_markers` (`orchestrator/loop.py`) reads from the same merged env, with no
    # memory of this function's own prediction:
    #   - pinned `"1"` and declined (`declines_channel`), or pinned `"1"` with the channel unset
    #     and unavailable (`channel_less`): the channel is never invoked (one of its two keys is
    #     not `"1"` at launch), so the markers just go unhidden — no failure.
    #   - pinned `"1"` *and* `BAJUTSU_CONTROL_CHANNEL` also pinned `"1"`, landing in
    #     `channel_less`: both keys read `"1"` at launch, so the run loop invokes the channel
    #     anyway, and `apply_capability` fails the scenario loudly against a collector that
    #     structurally cannot answer — this function's own "can't carry it" verdict never reaches
    #     the launch env to stop it.
    touch_pinned_on = {id(s) for s in wants_markers if _env(s).get("BAJUTSU_TOUCH_MARKERS") == "1"}
    channel_pinned_on = {
        id(s) for s in wants_markers if _env(s).get("BAJUTSU_CONTROL_CHANNEL") == "1"
    }
    channel_less_will_fail = channel_less & touch_pinned_on & channel_pinned_on
    # A scenario with no `visual` verdict is in none of the partitions above, so the loop below would
    # `setdefault` its marker key like any other. That is wrong for one of them: a scenario whose
    # merged env already pins `BAJUTSU_CONTROL_CHANNEL` to `"1"` (on the scenario itself or its
    # target) already carries half the pair `_hides_touch_markers` reads, and that predicate reads
    # the merged launch env alone — it never consults `visual`. Writing the marker key would
    # complete that pair and make the run loop invoke the channel on a scenario that asked for
    # neither, failing it against a collector that may carry none. Nothing here can take that pin
    # back, so the markers stay off instead, announced below.
    marker_would_arm_unasked = {
        id(s)
        for s in scenarios
        if id(s) not in wants_marker_ids
        and "BAJUTSU_TOUCH_MARKERS" not in _env(s)
        and _env(s).get("BAJUTSU_CONTROL_CHANNEL") == "1"
    }
    pinned_unhidden = (touch_pinned_on & declines_channel) | (
        touch_pinned_on & (channel_less - channel_less_will_fail)
    )
    channel_less_drops = channel_less - channel_less_will_fail - pinned_unhidden
    declines_drops = declines_channel - pinned_unhidden
    if armed:
        typer.echo(
            "note: the scenario(s) whose verdict compares a screenshot need the in-app control "
            "channel, so the markers can be hidden for that one capture; the app must be built "
            "with -DBAJUTSU_ENABLE_CONTROL_CHANNEL or the scenario fails saying so: "
            f"{', '.join(s.name for s in needs_channel if id(s) in armed)}",
            err=True,
        )
    if channel_less_drops:
        typer.echo(
            "note: --touch-markers stays off for the scenario(s) whose verdict compares a "
            "screenshot, since the in-app control channel that would hide the markers for that "
            "one capture is not available here (it needs the xcuitest actuator, resolved per "
            "scenario, with network collection on): "
            f"{', '.join(s.name for s in needs_channel if id(s) in channel_less_drops)}",
            err=True,
        )
    if declines_drops:
        declined_names = sorted(s.name for s in wants_markers if id(s) in declines_drops)
        typer.echo(
            "note: --touch-markers stays off for the scenario(s) whose effective launch "
            "environment already declines BAJUTSU_CONTROL_CHANNEL (a pin on the scenario itself "
            "or its target), since nothing would then hide the markers for the capture their "
            f"`visual` verdict compares: {', '.join(declined_names)}",
            err=True,
        )
    if pinned_unhidden:
        pinned_names = sorted(s.name for s in wants_markers if id(s) in pinned_unhidden)
        typer.echo(
            "note: the scenario(s) whose effective launch environment already pins "
            "BAJUTSU_TOUCH_MARKERS (on the scenario itself or its target) keep drawing markers "
            "even though nothing here can hide them for the capture their `visual` verdict "
            f"compares: {', '.join(pinned_names)}",
            err=True,
        )
    if channel_less_will_fail:
        failing_names = sorted(s.name for s in wants_markers if id(s) in channel_less_will_fail)
        typer.echo(
            "note: the scenario(s) whose effective launch environment already pins both "
            "BAJUTSU_TOUCH_MARKERS and BAJUTSU_CONTROL_CHANNEL (on the scenario itself or its "
            "target) will fail: the run loop reads both keys and tries the channel regardless of "
            f"this run's own actuator, which cannot carry it: {', '.join(failing_names)}",
            err=True,
        )
    if marker_would_arm_unasked:
        unasked_names = sorted(s.name for s in scenarios if id(s) in marker_would_arm_unasked)
        typer.echo(
            "note: --touch-markers stays off for the scenario(s) whose effective launch "
            "environment already pins BAJUTSU_CONTROL_CHANNEL (on the scenario itself or its "
            "target) but have no `visual` verdict to correct, since adding the marker key would "
            "complete the pair the run loop reads and invoke the channel on any such scenario "
            "whose `expect` runs against a baselines directory, which asked for neither: "
            f"{', '.join(unasked_names)}",
            err=True,
        )
    for s in scenarios:
        if id(s) in pinned_unhidden or id(s) in channel_less_will_fail:
            # The merged env already pins the marker key to "1": a scenario's own pin makes
            # `setdefault` below a no-op, and a target-only pin needs nothing written to the
            # scenario at all, so either way there is nothing to do here.
            continue
        if id(s) in declines_channel or id(s) in channel_less:
            continue  # no channel to hide the markers: no markers, matching the notes above
        if id(s) in marker_would_arm_unasked:
            continue  # setting the marker key here is what would arm the channel: leave it unset
        if id(s) in merged_off:
            # The merged env already resolves the marker key to "0" — the scenario's own pin (a
            # `setdefault` below would be a no-op anyway) or, since BE-0365 unit 3, a target-level
            # one with nothing of the scenario's own to make `setdefault` a no-op against.
            continue
        s.preconditions.launch_env.setdefault("BAJUTSU_TOUCH_MARKERS", "1")
        if id(s) in armed:
            s.preconditions.launch_env.setdefault("BAJUTSU_CONTROL_CHANNEL", "1")


def _channel_available_for(
    backends: list[str], network: bool, available: Callable[[str], bool] = default_available
) -> Callable[[Scenario], bool]:
    """`_apply_touch_markers`'s `channel_available`: can *this* scenario carry BE-0365's channel?

    The channel rides the network collector and the app-side poll loop BajutsuKit ships only for a
    real Simulator process — the `xcuitest` actuator, not `fake`, whose collector nothing ever
    polls, not `playwright`, which observes network through the driver, and not `adb`, whose lease
    holds the same external receiver iOS reports to (BE-0283) but has no such poll loop to answer a
    command. That last one is why this selector, rather than the collector's shape, is what keeps
    the channel to `xcuitest` (`_hides_touch_markers`, `orchestrator/loop.py`).

    Both selectors have to answer `xcuitest`, because a multi-candidate `--backend` can disagree
    with itself in either direction and only one of the two answers provisions a collector.
    `select_actuator_for_scenario` is the per-scenario one the run loop resolves each actuator with
    (BE-0240), given the requested `backends` rather than the run-level first choice, so a scenario
    escalating *away* from `xcuitest` is not armed for an acknowledgement its collector will never
    send. `select_actuator` is the run-level one `runner/pool.py` pre-starts the collectors from
    (`if network and not pool_env.observes_network_via_driver()`): a scenario escalating *toward*
    `xcuitest` under a run whose first choice observes network through the driver — `--backend
    web,ios` — leases a `None` collector out of that never-filled dict, so arming it there would
    fail the scenario on a channel that structurally cannot exist. The conjunct is deliberately
    stricter than that one condition: requiring `xcuitest` at both levels keeps this predicate from
    having to track a provisioning decision made in another module from another actuator.

    `network` is the first conjunct so `--no-network` short-circuits before either selector runs;
    neither can newly raise here, since `_select_actuator_or_exit` already ran
    `select_actuator(backends)` with the same `available` and converted its `RuntimeError` into
    `Exit(2)`.

    Args:
        available: injected by the tests, the way `select_actuator_for_scenario` takes it — the
            real predicate gates on tooling the host running the suite has no reason to carry.
    """
    return lambda s: (
        network
        and select_actuator(backends, available) == "xcuitest"
        and select_actuator_for_scenario(backends, s, available) == "xcuitest"
    )


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
class _TargetSetup:
    """One declared target, brought up as far as `run` can before the pools exist (BE-0428).

    Everything here is resolved per target because the underlying config is: which actuator runs
    it, which device provider hands it a device, and how many lanes that device spec yields. A
    single-target run holds exactly one of these — the primary — so the path through `_dispatch` is
    the same either way.
    """

    name: str
    eff: Effective
    actuator: str
    backends: list[str]
    device: DeviceLease
    udids: list[str]
    workers: int


def _acquire_targets(
    target_effs: Mapping[str, Effective],
    backend: str,
    engines: list[str],
    udid: str,
    workers: int,
) -> dict[str, _TargetSetup]:
    """Select an actuator and acquire a device for every declared target, or release what we took.

    `acquire_device` reads the target's own `deviceProvider` (BE-0236), so it runs once per target
    rather than once per run: two targets can be served by two different providers, and a cloud
    device reserved for one of them must be handed back even if a later target's acquisition fails.
    """
    setups: dict[str, _TargetSetup] = {}
    try:
        for name, eff in target_effs.items():
            actuator, backends = _select_actuator(backend, eff, engines)
            device = acquire_device(eff, udid)
            udids, lanes = _resolve_lanes(
                actuator,
                device.udid_spec,
                workers,
                environment_for(actuator, device.udid_spec).resolve_device,
            )
            setups[name] = _TargetSetup(
                name=name,
                eff=eff,
                actuator=actuator,
                backends=backends,
                device=device,
                udids=udids,
                workers=lanes,
            )
    except BaseException:
        _release_devices(setups)
        raise
    return setups


def _pool_demand(scenarios: list[Scenario], setups: Mapping[str, _TargetSetup]) -> dict[str, int]:
    """The most devices any one scenario needs from each pool at once (BE-0428).

    Targets sharing a pool share its device queue, and a scenario holds every declared target's
    lease for its whole length, so two targets on one pool need two devices from it.
    """
    demand: dict[str, int] = {}
    for s in scenarios:
        per_pool: dict[str, int] = {}
        for name in s.targets:
            key = setups[name].actuator
            per_pool[key] = per_pool.get(key, 0) + 1
        for key, n in per_pool.items():
            demand[key] = max(demand.get(key, 0), n)
    return demand


def _resolve_multi_target_workers(
    scenarios: list[Scenario], setups: Mapping[str, _TargetSetup], workers: int
) -> int:
    """Cap `--workers` so concurrent scenarios cannot starve each other of devices (BE-0428).

    Each worker holds every one of its scenario's declared targets for that scenario's whole
    length, so N workers each needing k devices from one platform's pool need N*k devices there.
    The pools are acquired in a fixed order, which rules out a circular wait — but not a pool that
    simply never has a free device left, so the worker count is capped rather than left to block.
    Refusing outright would be worse: `--workers` is a throughput knob, and quietly running a
    correct suite more slowly beats failing a run that has enough devices to finish.
    """
    for actuator, needed in _pool_demand(scenarios, setups).items():
        lanes = next(len(s.udids) for s in setups.values() if s.actuator == actuator)
        if needed > lanes:
            typer.echo(
                f"a scenario declares {needed} targets served by the {actuator} pool, but only "
                f"{lanes} device lane(s) are available there — pass more devices via --udid, or "
                "raise --workers to widen a web pool"
            )
            raise typer.Exit(2)
        workers = min(workers, lanes // needed)
    return max(1, workers)


def _reject_incompatible_actuator_sharing(setups: Mapping[str, _TargetSetup]) -> None:
    """Refuse two same-actuator targets whose providers resolved different devices (BE-0428).

    `_open_pools` builds one pool per actuator from whichever target it sees first, and every
    later target sharing that actuator draws from that same pool — correct when they share one
    `deviceProvider` (the common case: both `booted`, or both naming the same explicit `--udid`),
    since they then resolve the same `udid_spec` and the pool is exactly the device catalog either
    one would have built. It is wrong when a second target's own provider (BE-0236) reserved a
    *different* device: that target would run on the first target's device instead of its own,
    with its own reservation left sitting unused (and, for a device-cloud provider, billed) for the
    whole run. Checked here, once every target's device is already resolved, rather than left to
    surface as a silently wrong device — prime directive 2 rules out the substitution outright.
    """
    seen: dict[str, tuple[str, str]] = {}  # actuator -> (first target name, its udid_spec)
    for name, setup in setups.items():
        first_name, first_spec = seen.setdefault(setup.actuator, (name, setup.device.udid_spec))
        if setup.device.udid_spec != first_spec:
            typer.echo(
                f"targets '{first_name}' and '{name}' both resolve the '{setup.actuator}' "
                f"backend, but their device providers reserved different devices "
                f"({first_spec!r} vs {setup.device.udid_spec!r}) — a run does not yet support two "
                "device pools for one actuator, so give them the same deviceProvider/udid, or "
                "put one on a different actuator"
            )
            raise typer.Exit(2)


def _release_devices(setups: Mapping[str, _TargetSetup]) -> None:
    """Hand every target's device back to its provider (a no-op for the local one).

    Warn-only, never propagated: a provider's teardown failure must not flip or mask the
    machine-only verdict, the same rule the post-verdict zip / upload steps honor — a leaked device
    is loud on stderr, not a crash. One failure never stops the remaining targets being released.
    """
    for name, setup in setups.items():
        try:
            setup.device.release()
        except Exception as exc:
            typer.echo(
                f"warning: device release for target '{name}' failed ({exc}); "
                "a reserved device may be leaked",
                err=True,
            )


@dataclass(frozen=True)
class _RunPlan:
    """Everything a resolved `run` needs to dispatch and report — plain data, no behavior.

    `run` fills this from the option flags via the `_resolve_*`/`_load_*` helpers, then hands it to
    `_dispatch` and `_finish`. It carries resolved inputs only (no methods, no `self`-mutation), so
    each helper stays unit-testable without a Simulator (BE-0143).
    """

    eff: Effective
    config_source: dict[str, str] | None
    target_name: str
    # Every target this run's scenarios declare, each already actuator-selected and device-leased
    # (BE-0428), keyed by name and including the primary. A run whose scenarios declare none holds
    # only the primary, and the singular fields below are its own — so the single-target path and
    # the cross-browser matrix read exactly what they always have.
    targets: dict[str, _TargetSetup]
    scenarios: list[Scenario]
    description: str | None
    source_name: str
    # Each scenario's report plan source: its own file name, verbatim YAML (comments
    # intact), and steps' original line numbers, keyed by its declared name — see `_expand_file`.
    plan_sources: dict[str, ScenarioPlanSource]
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
    alert_guard_for: AlertGuardFor | None
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
    # `--score`: emit the app's entry-screen convention score once (doctor's grade, inline) so CI needs
    # no separate `doctor` cold-spawn. Diagnostic only — never on the verdict path.
    score: bool
    # The run-history partition stamped into the manifest (BE-0404 unit 2). Empty = the config's own
    # name, which `serve` derives; a bare CLI run keeps whatever the operator passed.
    label: str
    # Reports whether a `SIGTERM` has asked this run to stop (BE-0370). The pipeline reads it at
    # each scenario, step, and condition-wait boundary and fails whatever it did not finish, so a
    # cancelled run still writes its manifest and report instead of vanishing from the history.
    cancelled: CancelSource
    # Whether a backend-crash-triggered retry may force `preconditions.erase=True`
    # (`bajutsu/common/runner/pipeline.py`'s forced-erase retry). `erase is not False` — True for the default
    # (unset) and explicit `--erase`, False only for an explicit `--no-erase` — captured here, ahead of
    # `_filter_scenarios` resolving every scenario's `preconditions.erase` to a concrete bool, since
    # that resolved value can no longer distinguish an explicit opt-out from "nobody asked".
    force_erase_on_retry: bool
    # `--trace-driver` (BE-0415): one `<sid>/driver_trace.json` per scenario, recording every
    # Python<->driver call. Diagnostic only, off by default, never on the verdict path.
    trace_driver: bool


def _print_score(score: Score) -> None:
    """Render the app's entry-screen convention score to stderr (the `run --score` inline of `doctor`).

    Written to stderr so stdout stays the machine-readable PASS/FAIL line — the same split progress and
    the AI-usage summary follow. Diagnostic only (prime directive 1): the grade never touches the run's
    exit code, which stays the assertions' machine-only verdict.
    """
    from bajutsu.common.doctor import render

    typer.echo("doctor (convention score):", err=True)
    typer.echo(render(score), err=True)


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
        from bajutsu.run import notify

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


def _open_pools(plan: _RunPlan) -> dict[str, tuple[LeaseFn, Callable[[], None]]]:
    """One device pool per distinct platform among this run's declared targets (BE-0428).

    A pool is backend-specific, not target-specific: it resolves one `RunEnvironment`'s device
    catalog and pre-starts that backend's own collectors, so an iOS target and a web target need two
    differently-built pools. Targets that share a backend share one pool and are told apart by the
    `Effective` each hands `lease()`, which is what decides the app that lease launches. A run whose
    scenarios declare no targets opens exactly one pool, exactly as before.

    Keyed by the resolved actuator rather than by the platform the proposal names: the actuator is
    what `device_pool` builds its environment from, and it already implies the platform, so this
    separates every pair of targets a platform key would and additionally separates two actuators
    on one platform — which a shared pool would serve with the wrong environment.

    `_reject_incompatible_actuator_sharing` has already refused a same-actuator pair whose
    providers resolved different devices by the time this runs, so every target sharing a pool
    here genuinely shares one provider's device(s).
    """
    pools: dict[str, tuple[LeaseFn, Callable[[], None]]] = {}
    try:
        for setup in plan.targets.values():
            if setup.actuator in pools:
                continue
            pools[setup.actuator] = device_pool(
                setup.udids,
                setup.backends,
                setup.eff,
                plan.runs_dir / plan.run_id,
                network=plan.network,
                log_predicate=plan.log_predicate or None,
                log_subsystem=plan.log_subsystem or _log_subsystem_default(setup.eff),
                secret_values=plan.secret_values,
                provision=setup.device.provision,
            )
    except BaseException:
        # `mid_run=True`: a pool that came up before a later one failed to must not have its own
        # teardown defect replace the bring-up error that's already propagating — the same
        # mask-the-real-fault risk `guarded_teardown`'s own `mid_run` flag exists to rule out.
        _close_pools(pools, mid_run=True)
        raise
    return pools


def _close_pools(
    pools: Mapping[str, tuple[LeaseFn, Callable[[], None]]], *, mid_run: bool = False
) -> None:
    """Shut every pool down, letting the first wiring defect surface once they all have.

    A pool's `shutdown()` raises the first lease-teardown defect it stashed (BE-0342), and that
    must still reach the operator — but not at the cost of leaving another platform's collectors
    listening, so the remaining pools are shut down first and the earliest defect re-raised after.

    `mid_run` (set by a caller unwinding an exception of its own, or by this function's own
    `finally`-time check below) downgrades that re-raise to a warning: a teardown defect raised from
    a `finally` while a different exception is already propagating would silently replace it, the
    same failure mode `guarded_teardown`'s own `mid_run` flag exists to rule out.
    """
    first: BaseException | None = None
    for actuator, (_lease, shutdown) in pools.items():
        try:
            shutdown()
        except Exception as exc:
            typer.echo(f"warning: shutting down the {actuator} pool failed ({exc})", err=True)
            first = first or exc
    if first is None:
        return
    if mid_run or sys.exc_info()[0] is not None:
        typer.echo(f"warning: device pool teardown failed ({first}); continuing", err=True)
        return
    raise first


def _dispatch_single(
    plan: _RunPlan,
    progress_fn: Callable[[str], None] | None,
    exec_decision: dict[str, str | None] | None,
) -> tuple[list[RunResult], Path]:
    """The single-engine path — exactly today's flow: one pool per platform, one `run_and_report`."""
    pools = _open_pools(plan)
    lease = pools[plan.actuator][0]
    try:
        return run_and_report(
            plan.eff,
            plan.scenarios,
            lease,
            plan.runs_dir,
            plan.run_id,
            targets={
                name: TargetPool(
                    eff=setup.eff,
                    lease=pools[setup.actuator][0],
                    actuator=setup.actuator,
                    udid_spec=setup.device.udid_spec,
                )
                for name, setup in plan.targets.items()
            },
            alert_guard_for=plan.alert_guard_for,
            workers=plan.workers,
            bindings=plan.secret_bindings,
            secret_values=plan.secret_values,
            source_name=plan.source_name,
            description=plan.description,
            plan_sources=plan.plan_sources,
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
            label=plan.label or None,
            golden_context=plan.golden_context,
            lease_udid_spec=plan.udid_spec,
            # `--score`: fold doctor's entry-screen grade into this run's own first lease, so CI reads
            # the Ready/Partial/Blocked tell without a separate `doctor` that cold-spawns a second
            # XCUITest runner. Off by default, so an ordinary run is unchanged.
            on_score=_print_score if plan.score else None,
            trace_driver=plan.trace_driver,
            force_erase_on_retry=plan.force_erase_on_retry,
            cancelled=plan.cancelled,
        )
    finally:
        _close_pools(pools)


def _dispatch_matrix(
    plan: _RunPlan,
    progress_fn: Callable[[str], None] | None,
    exec_decision: dict[str, str | None] | None,
) -> tuple[list[RunResult], Path]:
    """The cross-browser matrix (BE-0076): one pass per engine against its own pool.

    Evidence lands under run_dir/<engine>/<sid>; the pipeline assembles ONE report whose matrix
    aggregates the per-engine verdicts (all-must-pass, machine-only).
    """

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
                alert_guard_for=plan.alert_guard_for,
                workers=plan.workers,
                run_dir=engine_run_dir,
                bindings=plan.secret_bindings,
                secret_values=plan.secret_values,
                progress=progress_fn,
                baselines_dir=plan.baselines_dir,
                schemas_dir=plan.schemas_dir,
                actuator=plan.actuator,
                golden_context=plan.golden_context,
                # Each engine pass scores its own entry screen once (`--score`); off by default.
                on_score=_print_score if plan.score else None,
                trace_driver=plan.trace_driver,
                force_erase_on_retry=plan.force_erase_on_retry,
                cancelled=plan.cancelled,
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
        plan_sources=plan.plan_sources,
        secret_values=plan.secret_values,
        label=plan.label or None,
        config_source=plan.config_source,
        exec_provenance=exec_decision,
        cancelled=plan.cancelled,
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
    from bajutsu.common.run_meta.object_store import (
        object_store_from_uri,
        parse_store_uri,
        upload_tree,
    )

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


def _finish(plan: _RunPlan, results: list[RunResult], manifest: Path) -> None:
    """Emit the verdict and every post-verdict step, then exit with the machine-only code.

    Order is load-bearing: the PASS/FAIL verdict and exit code are decided first (machine-only, no
    LLM); `--zip` and `--evidence-store` run strictly after and can only warn, never flip the verdict
    (BE-0060/BE-0110).
    """
    ok = all(r.ok for r in results)
    github_actions.emit(results, manifest.parent / "report.html")  # annotations + summary in CI
    # Webhook: post-verdict notification (BE-0099).
    if plan.eff.notify:
        from bajutsu.run import notify

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
    raise typer.Exit(0 if ok else 1)


def run(
    # --- Target & scenario selection ---
    target_name: str = typer.Option(
        "",
        "--target",
        help="the target to run against; required unless every --scenario file declares its own "
        "`targets:` (BE-0428), in which case --scenario is required in its place and the file's "
        "own names are resolved from the config",
    ),
    scenario: Annotated[
        list[str] | None,
        typer.Option(
            "--scenario",
            help=(
                "run only these *.yaml (repeat --scenario to run several in one process, sharing "
                "one warm runner); overrides the target's configured scenarios dir"
            ),
        ),
    ] = None,
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
    ios_tipkit_handling: bool | None = typer.Option(
        None,
        "--ios-tipkit-handling/--no-ios-tipkit-handling",
        help="dismiss a blocking iOS TipKit tip (default: per-scenario, off)",
    ),
    # --- Alerts, capture & logging ---
    system_alert_handling: bool | None = typer.Option(
        None,
        "--system-alert-handling/--no-system-alert-handling",
        help="override every scenario's systemAlertHandling (default: per-scenario, on; the guard "
        "is fully deterministic and makes no model call)",
    ),
    alert_poll_interval: float | None = typer.Option(
        None,
        "--alert-poll-interval",
        help="seconds between the native system-alert presence queries (a scenario's own wins)",
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
    score: bool = typer.Option(
        False,
        "--score/--no-score",
        help="print the app's entry-screen convention score (doctor's Ready/Partial/Blocked grade) "
        "to stderr, computed from this run's own first launch — so CI reads the tell without a "
        "separate `doctor` that cold-spawns a second runner. Diagnostic only; never affects pass/fail",
    ),
    trace_driver: bool = typer.Option(
        False,
        "--trace-driver",
        help="write <sid>/driver_trace.json per scenario, recording every Python<->driver call — "
        "the driver method invoked, its host-device round trips, and (on Android) which fell back "
        "to a subprocess — attributed to the step it happened during. Diagnostic only; never "
        "affects pass/fail",
    ),
    touch_markers: bool = typer.Option(
        False,
        "--touch-markers/--no-touch-markers",
        help="draw a marker at each touch the app receives, so the recorded video and each step's "
        "screenshot show where the gesture landed. Needs an app that links BajutsuKit; the marker "
        "is a layer, never an accessibility element, so no selector can see it. No assertion reads "
        "the markers. A scenario carrying a `visual` assertion hides them for that one capture over "
        "the in-app control channel, which the app must be built to carry; where that channel is "
        "unavailable (no network collection, or a run or scenario on a non-xcuitest actuator) this "
        "flag draws no markers for that scenario at all. Not verdict-neutral where the channel is "
        "armed: a command the app never acknowledges fails that scenario",
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
        DEFAULT_RUNS_DIR,
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
    label: str = typer.Option(
        "",
        "--label",
        help="tag this run's history entry with a short free-text label, so runs of two configs "
        "stay readable apart (BE-0404). Opaque to the tool — never parsed or matched against "
        "config; defaults to the config's own name",
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
    only fires to clear an OS prompt that blocked a step — see each scenario's `systemAlertHandling`.
    """
    if len(label) > MAX_LABEL_LENGTH:
        # Rejected, never truncated (BE-0404 unit 2): an operator learns the label was refused
        # instead of finding a silently shortened one in the history.
        raise typer.BadParameter(
            f"a label must be at most {MAX_LABEL_LENGTH} characters", param_hint="--label"
        )
    # Resolve the run's inputs from the flags — each step is an independently testable helper — then
    # assemble the plan and hand it to dispatch/finish. `run` itself stays a thin sequence.
    #
    # `--target` may be omitted for a run driven entirely by self-declaring scenarios (BE-0428), so
    # the primary target is resolved first, from the flag or from the `--scenario` files themselves;
    # everything below — the config load, the pool, the evidence dirs — is unchanged once it has one.
    explicit_target = bool(target_name)
    target_name = _resolve_primary_target(target_name, scenario or [])
    loaded, eff, config_source, engines = _resolve_config_and_engines(
        config,
        target_name,
        offline=config_offline,
        require_pinned=require_pinned_config,
        headed=headed,
        browser=browser,
        browsers=browsers,
    )
    scenarios, description, source_name, files, plan_sources = _load_scenarios(
        eff, scenario or [], target_name
    )
    scenarios = _filter_scenarios(
        scenarios,
        tag,
        exclude,
        erase,
        eff.run_defaults.erase,
        ios_tipkit_handling,
        eff.run_defaults.ios_tip_kit_handling,
    )
    # After filtering, so `--tag`/`--exclude` selecting away a self-declaring scenario in a suite
    # leaves the rest of the suite resolving exactly as it did before that file was added (BE-0428)
    # — these checks speak about the scenarios this run will actually attempt.
    _check_target_membership(scenarios, target_name, explicit=explicit_target)
    _reject_legacy_without_target(scenarios, target_name, explicit=explicit_target)
    target_effs = _resolve_target_effs(
        loaded, scenarios, target_name, eff, headed=headed, browser=browser
    )
    # Every declared target's own `secrets` names, not only the primary's — a `${secrets.X}` a step
    # routed elsewhere uses may be declared only on that target's own config (BE-0428).
    secret_bindings, secret_values = _resolve_secrets(target_effs.values())
    _reject_web_flags_across_targets(target_effs, headed=headed, browser=browser, browsers=browsers)
    _reject_cross_browser_matrix_with_targets(scenarios, engines)
    _reject_bad_target_config_hooks(target_effs, scenarios, target_name)
    # Where this target's devices come from is a seam (BE-0236): the provider `acquire` returns the
    # udid spec the lanes resolve against (the `--udid` flag verbatim for the default local provider,
    # a reserved serial / endpoint for a device cloud) plus what it already did to the device
    # (`provision`). Acquired before the `try` so its release runs even on a setup-time error below;
    # off the run/CI verdict path — no LLM, no assertion input. Once per declared target, since the
    # provider — like the actuator and the device lanes it feeds — is per-target config (BE-0428).
    #
    # Web has no simctl udid: `--workers N` is N near-free BrowserContext lanes (BE-0054); for
    # idb, `--udid` is a concrete comma list capped to the pool size. (The "booted" default is
    # unused on web.) How a device handle resolves is the platform's, behind the Environment seam
    # (BE-0256): Android via adb, the iOS family via simctl — no `actuator == "adb"` branch here.
    setups = _acquire_targets(target_effs, backend, engines, udid, workers)
    primary = setups[target_name]
    actuator, backends = primary.actuator, primary.backends
    udids = primary.udids
    lease = primary.device
    try:
        # Every target's device is already reserved by here, so a rejection past this point must
        # still release them — including this one, which can exit 2 on a pool too small for the
        # scenario's own target count (BE-0428).
        _reject_incompatible_actuator_sharing(setups)
        workers = _resolve_multi_target_workers(scenarios, setups, primary.workers)
        _apply_system_alert_handling(
            scenarios, resolve_system_alert_handling_flag(system_alert_handling)
        )
        alert_guard_for = _alert_guard_factory(
            scenarios,
            eff,
            _flag_alert_policy(alert_poll_interval),
        )
        # Network collection resolves `--network/--no-network` over the target's `network` config,
        # then on (BE-0177); the resolved bool baked into mocks and the plan drives collection and
        # `request` waits.
        network = _resolve_network(network, eff.run_defaults.network)
        _apply_mocks(scenarios, network)
        # `backends`, never the run-level `actuator` resolved above: the predicate has to ask the
        # same selector the run loop resolves each scenario's actuator with (`_channel_available_for`).
        _apply_touch_markers(
            scenarios,
            touch_markers,
            channel_available=_channel_available_for(backends, network),
            target_launch_env=eff.launch_env,
        )
        baselines_dir, schemas_dir, gc = _resolve_evidence_dirs(
            baselines, schemas, goldens, eff, files[0]
        )
        # Answer a `SIGTERM` by asking the run to stop at its next safe boundary instead of dying
        # where it stands, leaving no manifest, no report, and no history row (BE-0370). The window
        # covers `_finish` too: the `FAIL <manifest>` line it prints is what `serve` reads this run's
        # id from, so a hard kill between the report and that line would still lose the run.
        with graceful_sigterm() as cancelled:
            plan = _RunPlan(
                eff=eff,
                config_source=config_source,
                target_name=target_name,
                targets=setups,
                scenarios=scenarios,
                description=description,
                source_name=source_name,
                plan_sources=plan_sources,
                engines=engines,
                actuator=actuator,
                backends=backends,
                udids=udids,
                udid_spec=lease.udid_spec,
                workers=workers,
                provision=lease.provision,
                alert_guard_for=alert_guard_for,
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
                label=label,
                evidence_store=evidence_store,
                upload_exec=upload_exec,
                score=score,
                trace_driver=trace_driver,
                # `erase` is the pre-`_filter_scenarios` CLI flag: None (unset) and explicit `--erase` both
                # mean "no operator opt-out", only `--no-erase` (False) does.
                force_erase_on_retry=erase is not False,
                cancelled=cancelled,
            )
            # No usage ledger and no token accounting here: since BE-0402 removed the alert guard's
            # vision fallback, nothing in `run` can reach a model, so there is nothing to attribute.
            results, manifest = _dispatch(plan)
            _finish(plan, results, manifest)
    finally:
        # Hand every target's device back to its provider (a no-op for the local one), even on
        # failure so a reserved cloud device is never leaked (BE-0236).
        _release_devices(setups)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(run)

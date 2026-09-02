"""`bajutsu run` — execute a scenario deterministically (the Tier-2 CI gate)."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from bajutsu.doctor import Score

import typer
from pydantic import ValidationError

from bajutsu import device_errors
from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    _load_effective_with_source,
    _log_subsystem_default,
    _resolve_browser,
    _select_actuator_or_exit,
    _start_launch_server_or_exit,
    _with_headed,
    resolve_system_alert_handling_flag,
)
from bajutsu.common.assertions import GoldenContext
from bajutsu.common.backends import select_actuator_for_scenario
from bajutsu.common.cancellation import CancelSource, graceful_sigterm
from bajutsu.common.orchestrator import (
    DEFAULT_ALERT_POLL_INTERVAL,
    AlertGuardConfig,
    RunResult,
)
from bajutsu.common.orchestrator.types import ResolvedAlertRule
from bajutsu.common.runner import device_pool, run_all, run_and_report, run_matrix_and_report
from bajutsu.common.runner.build import BuildError, build_if_missing
from bajutsu.common.runner.device_provider import acquire_device
from bajutsu.common.runner.types import AlertGuardFor
from bajutsu.common.scenario import (
    Scenario,
    SystemAlertHandling,
    SystemAlertHandlingField,
    SystemAlertRule,
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
from bajutsu.common.scenario.system_alerts import (
    UncoveredSystemAlertLocale,
    covered_languages,
    system_alert_label,
)
from bajutsu.config import WEB_ENGINES, Effective, IosConfig
from bajutsu.deprecations import warn_once
from bajutsu.github import actions as github_actions
from bajutsu.platform_lifecycle import ProvisionProfile, environment_for
from bajutsu.report.archive import archive_run_dir
from bajutsu.report.manifest import MAX_LABEL_LENGTH, _run_backend
from bajutsu.run_files import DEFAULT_RUNS_DIR
from bajutsu.run_id import new_run_id


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
    eff: Effective, scenario: list[str], target_name: str
) -> tuple[list[Scenario], str | None, str, list[Path]]:
    """Load and fully expand the run's scenarios: the `--scenario` files, or the target's dir.

    Each file's setup/component/data refs resolve relative to its own directory, then the expanded
    scenarios concatenate into one run. Returns the scenarios, the single-file description (None for
    a multi-file or directory run), the report's source label, and the source files.
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
    for path in files:
        expanded, file_desc = _expand_file(path, eff, root)
        scenarios.extend(expanded)
        if single:
            description = file_desc
    # The report's source label: the single file's name, else the root dir's name.
    source_name = files[0].name if single else root.name
    return scenarios, description, source_name, files


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
    """Each rule's prompt resolved to its identifying label pair and tap label, for `locale`.

    Raises:
        UncoveredSystemAlertLocale: a rule names a prompt the label table has no entry for under
            `locale`'s language — the same fail-loudly choice `handleSystemAlert`'s own
            `prompt`/`choice` resolution makes, rather than guessing at a label. Re-raised naming
            *this* surface, since the lookup's own message is phrased for that step.
    """
    resolved = []
    for rule in rules:
        try:
            grant = system_alert_label(rule.prompt, "grant", locale)
            deny = system_alert_label(rule.prompt, "deny", locale)
        except UncoveredSystemAlertLocale as exc:
            # The lookup's message names `handleSystemAlert` and offers its `sel.label` remedy —
            # neither of which a scenario reaching here need have written. Re-scope it to the guard
            # and to the guard's own in-kind remedy (a `labels` list), so a loud failure
            # names the surface that actually failed.
            covered = ", ".join(covered_languages(rule.prompt))
            raise UncoveredSystemAlertLocale(
                f"systemAlertHandling.rules prompt: {rule.prompt} has no known button labels for "
                f"locale {locale!r}; covered: {covered}. Give the guard an explicit `labels` "
                "list instead, or add the language to bajutsu/scenario/system_alerts.py"
            ) from exc
        tap_label = grant if rule.choice == "grant" else deny
        resolved.append(
            ResolvedAlertRule(identifying_labels=frozenset({grant, deny}), tap_label=tap_label)
        )
    return resolved


def _flag_alert_policy(labels: str, poll_interval: float | None) -> SystemAlertHandling | None:
    """The command-line layer of `systemAlertHandling`, or None when no flag declares one.

    Validated through the model the file layers use, so `--alert-labels ","` fails the same way an
    empty `labels:` list does rather than resolving to the dismissive default a flag was written to
    override (BE-0401). `rules` has no flag: an entry pairs a prompt with a choice, which one flag
    value cannot carry legibly. `visionInstruction` has none either since BE-0402 removed `run`'s
    vision fallback — every value a bare string flag could carry was the free-text form, so the flag
    was retired rather than left as a knob whose only outcomes are "no effect" and "abort the run".

    Raises:
        typer.Exit: a flag's value does not satisfy the schema, reported as a CLI error rather than a
            validation traceback.
    """
    fields: dict[str, Any] = {}
    if labels:
        # Strip each entry, so `--alert-labels "Allow, OK"` names two buttons rather than one
        # button and one that can never match; an entry left empty by the strip raises below.
        fields["labels"] = [label.strip() for label in labels.split(",")]
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
    inner_layers: list[SystemAlertHandling],
) -> None:
    """Notice each target rule that will answer a prompt inside a scenario answering for itself.

    *inner_layers* are the layers more specific than the target — the scenario's own policy and the
    command line's — since only a declaration from one of those makes the scenario one that answers
    for itself.

    Composition restores a case BE-0382 removed on purpose. The behavior is correct under the
    specificity ladder — a rule names a prompt, labels name no prompt at all — but BE-0382's
    objection was that it is *silent*: a project-wide edit changes a scenario that names no prompt.
    The notice keeps the composition and removes the silence. Only a rule the scenario does not
    already rule on is named, since its own rule shadows the target's for that prompt.

    Keyed on the scenario *and* the prompt, not the prompt alone: `warn_once` dedupes for the whole
    process, so a prompt-only code would warn for the first affected scenario of a run and pass over
    the rest in the very silence this removes.
    """
    answers_for_itself = any(layer.labels for layer in inner_layers)
    if not target_rules or not answers_for_itself:
        return
    ruled = {r.prompt for r in scenario_rules}
    for rule in target_rules:
        if rule.prompt in ruled:
            continue
        warn_once(
            f"systemAlertHandling.targetRule.{s.name}.{rule.prompt}",
            f"scenario {s.name!r} names its own system-alert buttons, and the target config's "
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
        "it steers only the AI-vision fallback, which `run` no longer has (BE-0402). Name the "
        "buttons with `labels: [...]`, or answer a covered prompt with `rules: [...]`. "
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
    the target config (BE-0177) — composed by the key's type (BE-0401): a list concatenates innermost
    layer first, so both layers' entries stay reachable; a scalar takes the innermost layer that
    supplies one. `rules` reaches only two of the three, since no flag can carry a prompt paired with
    a choice legibly.

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
        labels = [label for layer in layers for label in layer.labels]
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
        # The notice reads the scenario's and the flag's own declarations, not the concatenation
        # above: the concatenation carries the target's *own* labels, which would make a scenario
        # that declares nothing look like one answering for itself.
        _warn_target_rules_reach(
            s,
            scenario_rules,
            target_rules,
            [layer for layer in (scenario_policy, flag_policy) if layer is not None],
        )
        locale = s.preconditions.resolved_locale(eff.locale)
        rules = _resolve_rules([*scenario_rules, *target_rules], locale)

        return AlertGuardConfig(labels=labels, rules=rules, poll_interval=poll_interval)

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


def _visual_asserting_scenarios(scenarios: list[Scenario]) -> list[str]:
    """Names of the scenarios whose verdict reads a screenshot, from `expect` or from any step."""
    named = []
    for s in scenarios:
        assertions = [*s.expect, *(a for step in s.steps for a in step.assert_ or [])]
        if any(a.visual is not None for a in assertions):
            named.append(s.name)
    return named


def _apply_touch_markers(scenarios: list[Scenario], enabled: bool) -> None:
    """Ask BajutsuKit to draw a marker at each touch the app receives, via the launch env.

    Off unless asked for: the marker is drawn inside the app under test, so it belongs to a run
    someone is investigating rather than to every run. A scenario that already sets the variable
    keeps its own value, like the mocks above.

    A scenario whose verdict compares a screenshot is skipped rather than marked, and the skip is
    announced on stderr. The markers persist until the next gesture by design, so the image a
    `visual` assertion reads would carry a circle and a trail its baseline does not, failing the
    scenario for a reason that has nothing to do with the app; masking cannot rescue it either, since
    the marker follows the gesture instead of occupying a fixed region. Skipping is safe at exactly
    this granularity because the app is terminated and relaunched with **each scenario's own** launch
    env — on the warm-runner path as much as the cold one (`_reuse_live_runner`, BE-0291) — so a
    skipped scenario runs in a process where the hook was never installed. Narrowing further, to the
    steps within one scenario, is not possible today: the launch env is the only channel into the app
    and it is fixed for the life of the process.
    """
    if not enabled:
        return
    skipped = _visual_asserting_scenarios(scenarios)
    if skipped:
        typer.echo(
            "note: --touch-markers is off for the scenario(s) whose verdict compares a screenshot, "
            "since the markers would be drawn into the image a `visual` assertion reads: "
            f"{', '.join(skipped)}",
            err=True,
        )
    for s in scenarios:
        if s.name in skipped:
            continue
        s.preconditions.launch_env.setdefault("BAJUTSU_TOUCH_MARKERS", "1")


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
    each helper stays unit-testable without a Simulator (BE-0143).
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


def _print_score(score: Score) -> None:
    """Render the app's entry-screen convention score to stderr (the `run --score` inline of `doctor`).

    Written to stderr so stdout stays the machine-readable PASS/FAIL line — the same split progress and
    the AI-usage summary follow. Diagnostic only (prime directive 1): the grade never touches the run's
    exit code, which stays the assertions' machine-only verdict.
    """
    from bajutsu.doctor import render

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
            alert_guard_for=plan.alert_guard_for,
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
            label=plan.label or None,
            golden_context=plan.golden_context,
            lease_udid_spec=plan.udid_spec,
            # `--score`: fold doctor's entry-screen grade into this run's own first lease, so CI reads
            # the Ready/Partial/Blocked tell without a separate `doctor` that cold-spawns a second
            # XCUITest runner. Off by default, so an ordinary run is unchanged.
            on_score=_print_score if plan.score else None,
            force_erase_on_retry=plan.force_erase_on_retry,
            cancelled=plan.cancelled,
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
    raise typer.Exit(0 if ok else 1)


def run(
    # --- Target & scenario selection ---
    target_name: str = typer.Option(..., "--target"),
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
    alert_labels: str = typer.Option(
        "",
        "--alert-labels",
        help='comma-separated button labels the native alert path taps, e.g. "Allow,OK" '
        "(a scenario's own labels are tried first, then these, then the target config's)",
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
    touch_markers: bool = typer.Option(
        False,
        "--touch-markers/--no-touch-markers",
        help="draw a marker at each touch the app receives, so the recorded video and each step's "
        "screenshot show where the gesture landed. Needs an app that links BajutsuKit; the marker "
        "is a layer, never an accessibility element, so no selector can see it. Evidence only — no "
        "assertion reads the markers — and automatically off for a scenario carrying a `visual` "
        "assertion, whose screenshot comparison they would break",
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
    scenarios, description, source_name, files = _load_scenarios(eff, scenario or [], target_name)
    scenarios = _filter_scenarios(
        scenarios,
        tag,
        exclude,
        erase,
        eff.run_defaults.erase,
        ios_tipkit_handling,
        eff.run_defaults.ios_tip_kit_handling,
    )
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
        _apply_system_alert_handling(
            scenarios, resolve_system_alert_handling_flag(system_alert_handling)
        )
        alert_guard_for = _alert_guard_factory(
            scenarios,
            eff,
            _flag_alert_policy(alert_labels, alert_poll_interval),
        )
        # Network collection resolves `--network/--no-network` over the target's `network` config,
        # then on (BE-0177); the resolved bool baked into mocks and the plan drives collection and
        # `request` waits.
        network = _resolve_network(network, eff.run_defaults.network)
        _apply_mocks(scenarios, network)
        _apply_touch_markers(scenarios, touch_markers)
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

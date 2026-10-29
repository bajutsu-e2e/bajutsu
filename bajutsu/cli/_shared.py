"""Helpers shared across CLI command modules (config loading, backend parsing).

Command-specific helpers live with their command in `commands/<name>.py`; only the
genuinely cross-command pieces belong here, so adding a command rarely edits this file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, overload

import typer
from pydantic import ValidationError

from bajutsu.common.agents import ai_config, anthropic_client
from bajutsu.common.ai import credential_gap
from bajutsu.common.ai import disabled as ai_disabled
from bajutsu.common.backends import (
    backend_override,
    current_host_os,
    ensure_web_runtime,
    resolve_actuators,
    select_actuator,
)
from bajutsu.common.capability import capability_preflight
from bajutsu.common.capability.worker_capability import (
    WorkerCapability,
    WorkerCapabilityError,
    load_worker_capability,
)
from bajutsu.common.config import (
    AiConfig,
    Config,
    Effective,
    ResolvedPreconditions,
    WebConfig,
    check_preconditions,
    command_preconditions,
    ios_bundle_id,
    load_config_file,
    resolve,
    web_engine,
)
from bajutsu.common.config.schema.platform.web import WebBrowser
from bajutsu.common.config_source import (
    DEFAULT_CONFIG as DEFAULT_CONFIG,  # re-exported: the single owner is config_source (BE-0251)
)
from bajutsu.common.config_source import (
    is_full_sha,
    materialize,
    parse_config_spec,
    source_provenance,
)
from bajutsu.common.deprecations import warn_once
from bajutsu.common.devices.browsers import BROWSER_KINDS
from bajutsu.common.devices.runs import condition_path
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.github import GitHubAccessError
from bajutsu.common.run_meta.files import is_soft_deleted
from bajutsu.common.runner.launch_server import start_launch_server

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bajutsu.common.agents.alerts import ClaudeAlertLocator
    from bajutsu.common.devices.facts import DeviceFacts
    from bajutsu.common.drivers import base
    from bajutsu.common.orchestrator import AlertEvent
    from bajutsu.common.scenario import Scenario


@overload
def resolve_system_alert_handling_flag(
    system_alert_handling: bool | None, *, default: bool
) -> bool: ...
@overload
def resolve_system_alert_handling_flag(
    system_alert_handling: bool | None, *, default: None = None
) -> bool | None: ...
def resolve_system_alert_handling_flag(
    system_alert_handling: bool | None, *, default: bool | None = None
) -> bool | None:
    """Resolve the `--system-alert-handling` flag against each command's own unset behavior.

    `run` leaves it None (each scenario's own value applies), while `record` and `crawl` pass the
    target's `run.systemAlertHandling` as the default, on unless the target turns it off (BE-0452).
    Shared by all three so the flag behaves identically on each. The `--alert-handling` /
    `--dismiss-alerts` aliases this once merged were deleted with no replacement (BE-0401).
    """
    return default if system_alert_handling is None else system_alert_handling


def _secret_values(eff: Effective) -> list[str]:
    """The declared secrets resolved from the environment (the literal values to mask).

    The shared "resolve `eff.secrets` against `os.environ`" rule, so evidence redaction and AI-input
    redaction never drift in how they read secrets.
    """
    return [os.environ[n] for n in eff.secrets if n in os.environ]


def _warn_onscreen_secrets(eff: Effective) -> None:
    """Disclose that on-screen secrets leak into images before an AI path that binds them starts (BE-0151).

    `record` sends the live screenshot to the AI each turn; `triage --ai` sends the captured failure
    screenshot (if any), read from the run's `runs/` evidence. The image goes to the AI provider
    resolved from config (Anthropic / Bedrock / ant). `${secrets.X}` values are masked in *text*
    evidence (network, element tree, logs) by `Redactor`, but images cannot be pixel-masked — so a
    secret the app *displays* (a typed password, an OTP, PII) stays in the raw pixels. This warns
    plainly at the point secrets are bound; it changes no behavior (visual evidence is the point) —
    an author who wants to avoid the exposure skips AI authoring for the flow, or keeps the secret
    off-screen. A no-op when the target declares no `secrets:`.
    """
    if not eff.secrets:
        return
    names = ", ".join(eff.secrets)
    typer.echo(
        f"⚠️  on-screen secrets are not redacted from images: {names} are masked in text evidence "
        "(network, element tree, logs), but a secret the app shows on screen (a typed password, an "
        "OTP, displayed PII) stays in the raw pixels of the screenshot sent to the AI — the live "
        "screen each turn under record, the captured failure screenshot under triage --ai. That "
        "image goes to the AI provider you configured.",
        err=True,
    )


def _ai_redactor(eff: Effective) -> Redactor:
    """The run-scoped redactor for AI inputs (BE-0047), built like evidence's.

    Same construction as `FileSink`/`pipeline`: the target's `redact` keys plus the literal secret
    values resolved from the environment, so a secret in an element / instruction the model would
    see is masked exactly as it is in written evidence.
    """
    return Redactor(eff.redact, values=_secret_values(eff))


def _credential_gap_message(gap: str, eff: Effective) -> str:
    """A provider-specific, actionable message for a missing AI credential (BE-0047 fail-closed)."""
    if gap == ai_disabled.DISABLED:
        # The kill switch (BE-0394), not a broken environment — so the message names the setting and
        # how to re-enable an AI path. It cannot name the *file* that sets it: `Effective` carries the
        # resolved config with no path back to its source, so that would need `--config` threaded here.
        return (
            "AI is disabled for this target by ai.provider: none — no AI path may run. "
            "Set ai.provider to a provider (api-key / bedrock / ant / claude-code) in the config, "
            "or drop the setting to fall back to $BAJUTSU_AI_PROVIDER."
        )
    if gap == "bedrock-model":
        return (
            "no AI credential: the Bedrock provider needs a provider-prefixed model id "
            "(set ai.model in config, or $BAJUTSU_BEDROCK_MODEL); AWS credentials authenticate it."
        )
    if gap == anthropic_client.ANT_CLI_MISSING:
        return (
            "no AI credential: the ant provider needs the Anthropic CLI — install `ant` and run "
            "`ant auth login`, or set ai.provider to api-key / bedrock."
        )
    if gap == anthropic_client.ANT_CLI_UNAUTHENTICATED:
        # Also returned when the token probe fails to exec or times out — the wording stays accurate
        # for that case too, while `ant auth login` remains the primary fix.
        return (
            "no AI credential: the Anthropic CLI (`ant`) has no active credential or could not be "
            "read — run `ant auth login`."
        )
    return (
        f"no AI credential: set ${anthropic_client.key_env(eff.ai)} (the env var named by "
        "ai.keyEnv). Bajutsu's AI paths never fall back to a hosted default (BE-0047)."
    )


def _require_ai_credential(eff: Effective) -> None:
    """Fail closed when the resolved provider has no usable credential (BE-0047).

    Raises a clean exit-2 so an AI entry point never constructs a client that would round-trip to a
    hosted default. A no-op when a credential is present.
    """
    gap = credential_gap(eff.ai)
    if gap is not None:
        typer.echo(_credential_gap_message(gap, eff))
        raise typer.Exit(2)


def _install_usage_ledger(eff: Effective, command: str, *, scenario: str | None = None) -> None:
    """Install the AI usage/cost ledger and bind this command's attribution (BE-0196).

    The one-line entry point every AI CLI command shares: configure the ledger from `eff.ai`, then
    bind `command` (and an optional `scenario`) so each recorded event says what its tokens were
    spent on. Reporting only — never on the deterministic verdict path. `run` does not use this and
    installs no ledger: since BE-0402 nothing in it reaches a model, so it has nothing to attribute.
    """
    from bajutsu.common.analytics import ledger as usage_ledger

    usage_ledger.configure_from_ai_config(eff.ai)
    usage_ledger.bind_command(command, scenario=scenario)


def resolve_run_dir(run: str, runs_root: str) -> Path:
    """Resolve a run id or path to its directory.

    A bare id (``r1``) resolves under *runs_root*; an absolute or multi-segment value
    (``/abs/run``, ``runs/r1``) is taken verbatim — so a mistyped path is never silently
    re-rooted under the runs dir. Shared by ``export`` and ``report``.

    Returns:
        The resolved run directory path (not checked for existence).
    """
    p = Path(run)
    return p if p.is_absolute() or len(p.parts) > 1 else Path(runs_root) / run


def read_manifests(runs_dir: Path) -> list[dict[str, object]]:
    """The parsed `manifest.json` of each run under *runs_dir*; unreadable/malformed ones are skipped.

    Shared by the read-only run-history readers (`audit --history`, `stats`): a run that can't be
    parsed carries no usable outcome, so dropping it here matches those tools' advisory tolerance —
    they never gate on completeness.
    """
    manifests: list[dict[str, object]] = []
    for d in sorted(runs_dir.iterdir()):
        manifest = d / "manifest.json"
        if not (d.is_dir() and manifest.is_file()) or is_soft_deleted(d):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            manifests.append(data)
    return manifests


def _load_effective(config: str, target_name: str) -> Effective:
    """Load and resolve the effective config for *target_name* (see `_load_effective_with_source`)."""
    return _load_effective_with_source(config, target_name)[0]


def _load_effective_with_source(
    config: str, target_name: str, *, offline: bool = False, require_pinned: bool = False
) -> tuple[Effective, dict[str, str] | None, Path | None]:
    """Load the effective config, the Git source provenance, and the checkout root when Git-sourced.

    *config* is a local path (today's behavior) or a Git source
    (``github:owner/repo@ref:path``, BE-0063), materialized at an immutable commit SHA; a Git-sourced
    config has its relative paths rebased against the checkout root. The tuple's second element is the
    repo + resolved commit (None for a local config) so `run` can stamp the manifest; the third is the
    materialized checkout root (None for a local config) so `run` can build the app from it.

    `offline` (``--config-offline``) materializes from the cache without touching the network.
    `require_pinned` (``--require-pinned-config``) rejects a Git source on a mutable ref — a gate must
    name an immutable commit SHA, since a branch (or even a tag) can move under it.

    Exits 2 (via ``typer.Exit``) for the user-friendly failures: a missing config file, an unknown
    target name, a private-repo access/auth failure (``GitHubAccessError``, BE-0224), and (with
    `require_pinned`) a Git source that isn't pinned to a commit SHA. Other errors — YAML parse /
    schema validation from ``load_config_file`` — propagate as exceptions.
    """
    loaded = _load_config_with_source(config, offline=offline, require_pinned=require_pinned)
    return _effective_for(loaded, target_name), loaded.source, loaded.root


@dataclass(frozen=True)
class LoadedConfig:
    """A parsed config file plus where it came from, ready to resolve any number of targets from.

    Separated from `_load_effective_with_source` so a multi-target scenario resolves every name it
    declares against the *same* materialized config and the same rebase root (BE-0428), instead of
    re-materializing a Git source once per declared target.
    """

    config: Config
    path: Path
    source: dict[str, str] | None
    root: Path | None


def _load_config_with_source(
    config: str, *, offline: bool = False, require_pinned: bool = False
) -> LoadedConfig:
    """Materialize and parse the config file, without resolving any target from it yet.

    See `_load_effective_with_source` for what *config*, `offline`, and `require_pinned` mean and
    which failures exit 2.
    """
    spec = parse_config_spec(config)
    source: dict[str, str] | None = None
    if spec is None:
        cfg_path = Path(config)
        root = None
    else:
        if require_pinned and not is_full_sha(spec.ref):
            typer.echo(
                f"--require-pinned-config: a Git config must pin a commit SHA, got ref "
                f"{spec.ref or '(default branch)'!r} (a branch or tag can move; pin @<40-hex-sha>)"
            )
            raise typer.Exit(2)
        try:
            mat = materialize(spec, offline=offline)
        except GitHubAccessError as e:
            # A private-repo access/auth failure gets the same friendly exit-2 as a missing config,
            # with the cause-naming message (BE-0224) instead of a raw HTTPError traceback.
            typer.echo(str(e))
            raise typer.Exit(2) from None
        cfg_path, root = mat.config_path, mat.root
        source = source_provenance(spec, mat)
    # The same friendly exit-2 for a missing config, whether local or a wrong in-repo path for a
    # Git source (the materialized tree exists but doesn't hold `spec.path`).
    if not cfg_path.exists():
        typer.echo(f"config not found: {config}")
        raise typer.Exit(2)
    # A component the target's hooks call stays inside the checkout for a Git source, and inside the
    # config file's own directory for a local file (BE-0452).
    cfg = load_config_file(cfg_path, root=root)
    return LoadedConfig(config=cfg, path=cfg_path, source=source, root=root)


def _effective_for(loaded: LoadedConfig, target_name: str) -> Effective:
    """Resolve one target out of an already-loaded config, rebased against the config's own root.

    Split out of `_load_effective_with_source` so every target a multi-target scenario declares is
    resolved — and rebased — exactly the same way the run's primary target is (BE-0428).
    """
    cfg_path, root = loaded.path, loaded.root
    try:
        eff = resolve(loaded.config, target_name)
    except KeyError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    # A config's relative paths resolve against the file that declares them, not the caller's cwd, so
    # the same config behaves the same wherever `bajutsu` runs from (BE-0242). A Git source rebases
    # against its checkout root, confined (a fetched config can carry attacker-authored path fields);
    # a local file rebases against its own directory, unconfined (an operator typing `--config` at
    # their own terminal is operator-trusted, BE-0121, and may point at a sibling). serve's own
    # file-browser bind (`bind_config`) hands the same config file to the same `Effective.rebased`
    # with `confine=True` instead — not because that file is less trusted (it can only be one
    # `_confined_config_path` already found inside `--root`), but as a defense-in-depth consistency
    # check applied regardless of bind source; see `Effective.rebased`'s docstring for the full
    # reasoning, including why this is independent of `build:` trust.
    if root is None:
        return eff.rebased(cfg_path.resolve().parent, confine=False)
    try:
        return eff.rebased(root)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def _refuse_out_in_checkout(out_path: Path, checkout_root: Path | None) -> None:
    """Refuse a generated artifact path that lands inside a read-only Git checkout (BE-0063).

    `record` / `crawl` take a Git source as **read-only input**: they may read its config and
    scenarios, but their output (a scenario, a screen map) goes to a local path, never into the
    SHA-keyed content-addressed cache. A no-op for a local config (`checkout_root` is None).

    Raises:
        typer.Exit: *out_path* resolves inside *checkout_root* (exit code 2).
    """
    if checkout_root is None:
        return
    if out_path.resolve().is_relative_to(checkout_root.resolve()):
        typer.echo(
            f"a Git --config is read-only: --out must be a local path, not inside the checkout "
            f"({out_path})"
        )
        raise typer.Exit(2)


def _backends(backend: str, fallback: list[str]) -> list[str]:
    """The backend list in force: *fallback* (the target's), or the checked `--backend`, or exit 2.

    `--backend` accepts the target's platform, its actuator, or `fake` (BE-0452,
    `backends.backend_override`).
    """
    try:
        return backend_override(backend, fallback)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def _note_fake_conditions(eff: Effective, backends: list[str]) -> bool:
    """Say once that `--backend fake` evaluates none of the target's conditions; True when it did.

    The fake driver has no device to read, so a run under it matches no `runsOn` condition (BE-0452).
    """
    if not eff.conditions or backends != ["fake"]:
        return False
    named = ", ".join(condition_path(eff.schema_platform, field) for field in eff.conditions)
    warn_once(
        "conditions.fake",
        f"note: --backend fake evaluates no device condition; {named} of target "
        f"'{eff.target}' is not checked",
    )
    return True


def parse_engine_flag(flag: str, value: str) -> str:
    """Parse one browser named on the command line as a value of `runsOn.browser.kind` (BE-0468).

    The flag is read by the model that reads the target's own field, so the command line and the
    config accept the same browsers. An unknown one is a clean exit 2, before it reaches Playwright.

    Args:
        flag: The flag that named the browser, for the message (`--browser`, `--browsers`).
        value: The browser kind as written.

    Raises:
        typer.Exit: *value* is not one of the known browser kinds (exit code 2).
    """
    try:
        WebBrowser.model_validate({"kind": value})
    except ValidationError:
        typer.echo(f"unknown {flag} {value!r}: use one of {', '.join(BROWSER_KINDS)}")
        raise typer.Exit(2) from None
    return value


def _precondition_checks(
    loaded: LoadedConfig, scenarios: list[Scenario], target_name: str, eff: Effective
) -> list[tuple[Scenario, list[Effective]]]:
    """Pair each scenario with the targets it drives, for `_refuse_bad_preconditions` (BE-0452).

    A scenario drives its declared `targets`, else the command's own *target_name* (*eff*). Shared
    by `run` and `audit` so both commands check one suite against the same targets.

    Returns:
        Each scenario with its driven targets. A scenario whose declared names are all unknown is
        left out: the target checks that follow name the typo, which an empty platform list here
        would only obscure.
    """
    effs = {target_name: eff}
    checks: list[tuple[Scenario, list[Effective]]] = []
    for s in scenarios:
        if not s.target_names:
            checks.append((s, [eff]))
            continue
        known = [n for n in s.target_names if n in effs or n in loaded.config.targets]
        if not known:
            continue
        for name in known:
            if name not in effs:
                effs[name] = _effective_for(loaded, name)
        checks.append((s, [effs[name] for name in known]))
    return checks


def _refuse_bad_preconditions(
    checks: list[tuple[Scenario, list[Effective]]], *, erase: bool | None
) -> None:
    """Refuse every scenario whose preconditions its driven targets cannot honor (BE-0452).

    Runs before any device work, on the scenarios as written: an `app` or iOS-only `run` field no
    driven platform has, and a merged `seedPhotos` without an effective erase
    (`check_preconditions`).

    Args:
        checks: Each scenario with the targets it drives.
        erase: The `--erase` / `--no-erase` flag, or None when absent.

    Raises:
        typer.Exit: Any scenario has a problem; each is printed with the scenario's name (exit 2).
    """
    problems: list[str] = []
    for s, targets in checks:
        problems += [
            f"{s.name}: {problem}"
            for problem in check_preconditions(s.preconditions, targets, erase_flag=erase)
        ]
    if problems:
        for problem in problems:
            typer.echo(problem)
        raise typer.Exit(2)


def _command_preconditions_or_exit(eff: Effective, *, erase: bool) -> ResolvedPreconditions:
    """The target's preconditions for `record`, `crawl`, or `repl`, or a clean exit 2.

    A target that seeds photos needs the wipe its `--no-erase` turned off; seeding nothing would
    ignore a field the config wrote (BE-0452), so the command refuses before any device work.

    Raises:
        typer.Exit: The target seeds photos and *erase* is false (exit code 2).
    """
    try:
        return command_preconditions(eff, erase=erase)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def _resolve_browser(eff: Effective, browser: str) -> Effective:
    """Apply a `--browser` flag over the target's `runsOn.browser.kind` (web backend, BE-0076).

    Precedence mirrors `--headed`: an explicit flag wins, else the target's config (already on
    `eff`), else the chromium default. The flag replaces the field, so a listed engine the target
    declares stops counting as a condition once the flag names one engine.

    Raises:
        typer.Exit: *browser* is set but not one of the known engines (exit code 2).
    """
    if not browser:
        return eff
    parse_engine_flag("--browser", browser)
    from bajutsu.run.device_matrix import apply_engine

    # `runsOn.browser` is a web-only field; a non-web target ignores the flag.
    return apply_engine(eff, browser)


def _without_engine_condition(eff: Effective) -> Effective:
    """*eff* with its listed `runsOn.browser.kind` no longer counted as a device condition.

    `--browsers` strips it here and `--browser` through `apply_engine`; both share one rule.
    """
    from bajutsu.run.device_matrix import without_engine_condition

    return without_engine_condition(eff)


def _resolve_language(eff: Effective, language: str) -> Effective:
    """Apply a `--language` flag over the resolved `ai.language` config (BE-0188).

    Precedence mirrors `--browser`: an explicit flag wins, else the config value (already on
    `eff.ai`), else the `auto` default. An unknown value is a clean exit 2 before it reaches an AI
    path. Governs only the language the model writes its generated prose in — never the
    deterministic run/CI verdict.

    Raises:
        typer.Exit: *language* is set but not one of the known languages (exit code 2).
    """
    # Normalize like the config/serve paths (`resolve_language`, `set_provider`) so the three input
    # surfaces agree: `--language JA` / ` ja ` is accepted, not rejected as unknown.
    normalized = language.strip().lower()
    if not normalized:
        return eff
    if normalized not in ai_config.LANGUAGES:
        known = ", ".join(ai_config.LANGUAGES)
        typer.echo(f"unknown --language {language!r}: use one of {known}")
        raise typer.Exit(2)
    if normalized == ai_config.DEFAULT_LANGUAGE:
        os.environ.pop(ai_config.LANGUAGE_ENV, None)
    return replace(eff, ai=replace(eff.ai or AiConfig(), language=normalized))


def _with_headed(eff: Effective, headed: bool | None) -> Effective:
    """Apply `--headed`/`--no-headed` over a web target's `driver.headless` (BE-0126, BE-0452).

    Web-only, like `--browser`: a non-web target's `driver` has no `headless` and ignores the flag.
    """
    if headed is None or not isinstance(eff.platform_config, WebConfig):
        return eff
    return replace(eff, platform_config=replace(eff.platform_config, headless=not headed))


def _log_subsystem_default(eff: Effective) -> str:
    """The default iOS log subsystem — the iOS bundle id, or empty for a non-iOS target (BE-0126).

    The device-log predicate scopes to the app's bundle id; a web target has no such subsystem.
    """
    return ios_bundle_id(eff)


def _select_actuator_or_exit(
    backend: str, eff: Effective, engines: list[str], worker: WorkerCapability | None = None
) -> tuple[str, list[str]]:
    """Resolve the actuator for the requested backends, provisioning any web runtime, or exit 2 (BE-0260).

    The backend bring-up all five commands (`run` / `crawl` / `record` / `audit` / `repl`) share:
    parse the backend list, auto-install the web runtime for each engine a web run needs
    (idempotent), then select the actuator — so a bad/unavailable backend exits cleanly (2) rather than crashing later
    on a missing device CLI. `run` passes its `--browsers` matrix and keeps the web-only-axis check
    at its call site; the other four pass no engines and provision the single resolved engine.

    Before selecting, every actuator the worker cannot run — its host cannot drive the driver, or
    `worker.yaml` omits it (BE-0450) — is dropped, and the returned backend list leaves it out so a
    later per-scenario selection cannot pick it again. A fallback list such as `[ios, web]` still
    resolves to `playwright` on Linux; only an empty remainder exits 2, on the named reason.

    Args:
        backend: The `--backend` override; empty means the target's configured list.
        eff: The target's effective configuration.
        engines: The web engines to provision; empty provisions the target's one engine.
        worker: The worker capability in force; the shipped default when None.

    Returns:
        The resolved actuator and the ordered backend list.
    """
    backends = _worker_backends(_backends(backend, eff.backend), worker)
    _note_fake_conditions(eff, backends)
    try:
        # A matrix provisions every listed engine (each install idempotent); with no engines the
        # single resolved default (with one, it already equals that).
        for engine in engines or [web_engine(eff)]:
            ensure_web_runtime(backends, engine)
        actuator = select_actuator(backends)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    return actuator, backends


def _worker_backends(backends: list[str], worker: WorkerCapability | None) -> list[str]:
    """*backends* without the actuators the worker cannot run, or exit 2 when none remains (BE-0450).

    The list comes back unchanged when nothing is dropped, so a run the worker fully supports keeps
    its platform tokens exactly as before.
    """
    if worker is None:
        worker = _load_worker_capability_or_exit(None)
    kept, reasons = capability_preflight.filter_actuators(
        resolve_actuators(backends), worker, current_host_os()
    )
    if not reasons:
        return backends
    if not kept:
        typer.echo(capability_preflight.worker_capability_failure(reasons))
        raise typer.Exit(2)
    # The fallback is the operator's own declaration at work, not a missing tool, so say why the
    # earlier backends were skipped rather than leave a run on a different platform unexplained.
    for reason in reasons:
        typer.echo(f"note: skipping a backend this worker cannot run: {reason}", err=True)
    return kept


def _load_worker_capability_or_exit(path: Path | None) -> WorkerCapability:
    """The worker capability named by `--worker-config` (the shipped default when None), or exit 2."""
    try:
        return load_worker_capability(path)
    except WorkerCapabilityError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


WORKER_CONFIG_HELP = (
    "the worker capability file (worker.yaml) this machine runs under, used in place of the shipped "
    "default (BE-0450); its environment is `local`, or `appium` to drive one platform's targets on "
    "the grid it names (BE-0452)"
)


def _load_device_worker_or_exit(path: Path | None) -> WorkerCapability:
    """The `--worker-config` in force for a command that drives a device, or exit 2 (BE-0450).

    Such a command drives devices on its own host, or through an `appium` grid (BE-0452), and never
    submits to a device cloud, so a file naming a batch provider describes a different kind of
    worker and is a usage error, not a silent override.
    """
    worker = _load_worker_capability_or_exit(path)
    if worker.is_batch:
        typer.echo(
            f"--worker-config: environment '{worker.environment}' is a device cloud; this command "
            "drives devices on its own host or through an appium grid, and needs "
            "`environment: local` or `environment: appium`"
        )
        raise typer.Exit(2)
    return worker


def _reject_url_udid(udid: str) -> None:
    """Refuse a URL as `--udid`: an Appium grid is named by an `appium` environment (BE-0452).

    Raises:
        typer.Exit: *udid* holds an http(s) URL (exit code 2).
    """
    if any(t.strip().startswith(("http://", "https://")) for t in udid.split(",")):
        typer.echo(
            "--udid no longer takes a URL: name the grid in an `appium` environment of the "
            "--worker-config file (BE-0452)"
        )
        raise typer.Exit(2)


def _with_worker_device(eff: Effective, worker: WorkerCapability) -> Effective:
    """*eff* with the device provider the worker's environment gives its platform (BE-0452).

    An `appium` environment hands its grid to the targets of the grid's platform; every other
    target keeps the built-in local provider. The platform is the target's declared one, so a target
    run under `--backend fake` still reads as its own platform here.
    """
    provider = worker.device_provider_for(eff.backend[0] if eff.backend else eff.platform)
    return replace(eff, device_provider=provider) if provider is not None else eff


def _device_udid_or_exit(eff: Effective, udid: str) -> str:
    """The udid spec *eff*'s device provider hands back for *udid*, or exit 2 (BE-0236, BE-0452).

    The single route every device-driving command takes to its device, as `run` does: the `--udid`
    pool for the local provider, the grid's endpoint for an `appium` one. Neither reserves anything,
    so there is no lease to release.
    """
    _reject_url_udid(udid)
    # Imported here: the provider registry reaches the platform environments, which only a command
    # that drives a device needs.
    from bajutsu.common.runner.device_provider import acquire_device

    try:
        return acquire_device(eff, udid).udid_spec
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def _first_run_or_exit(
    eff: Effective,
    actuator: str,
    udid: str,
    *,
    read_ios: Callable[[Sequence[str]], list[DeviceFacts]] | None = None,
    read_android: Callable[[Sequence[str]], list[DeviceFacts]] | None = None,
    read_browser: Callable[[str], DeviceFacts] | None = None,
) -> tuple[Effective, str]:
    """The run `record`, `crawl`, `repl`, and `doctor`'s probe take under the conditions, or exit 2.

    These commands never multiply runs (BE-0452): they take the first listed value and the newest
    major version the pool has within a range, on the first device of that run's eligible set
    (`first_run_device`). A target with no condition, and the fake backend, keep *udid* as given.

    Args:
        eff: The target's effective configuration.
        actuator: The selected actuator, which resolves each udid of the pool.
        udid: The udid spec the device provider handed back (a comma list for a pool).
        read_ios: Reads Simulator facts; injectable for tests.
        read_android: Reads Android device facts; injectable for tests.
        read_browser: Reads a web engine's launched version; injectable for tests.

    Returns:
        The configuration with the run's web engine applied, and the udid of the device it takes.

    Raises:
        typer.Exit: The target runs where no pool can be read, a device could not be read, or no
            available device fits (exit code 2).
    """
    if not eff.conditions or _note_fake_conditions(eff, ["fake"] if actuator == "fake" else []):
        return eff, udid
    from bajutsu.run.device_matrix import first_run_device

    try:
        return first_run_device(
            eff,
            actuator,
            udid,
            read_ios=read_ios,
            read_android=read_android,
            read_browser=read_browser,
        )
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def _start_launch_server_or_exit(
    eff: Effective,
    *,
    upload_exec: str | None = None,
    on_error: Callable[[], None] | None = None,
) -> tuple[Callable[[], None], dict[str, str | None] | None]:
    """Bring up the target's launch server (the web baseUrl host) if declared, or exit 2 (BE-0260).

    The launch-server bring-up all four commands share: start the server (reused if already
    serving, waiting on its readiness probe) and surface a startup failure as a clean exit 2. The
    call-site teardown difference stays at the call site (`run`'s `finally`, `crawl` / `record`'s
    `atexit`); `on_error` runs before the exit for the one caller (`audit`) that must first tear
    down an already-created device-pool lease.

    Returns:
        The server teardown callable and the exec-provenance decision (from `start_launch_server`).
    """
    try:
        return start_launch_server(eff, upload_exec=upload_exec)
    except RuntimeError as e:
        typer.echo(str(e))
        if on_error is not None:
            on_error()
        raise typer.Exit(2) from None


def _build_alert_locator(eff: Effective, redactor: Redactor) -> ClaudeAlertLocator | None:
    """The shared alert-guard locator, or None (with a note) when the provider reports a gap (BE-0260).

    The vision locator reaches Claude through the configured provider (BE-0053 / BE-0047), so a
    missing/insufficient credential prints an actionable note and returns None — the guard no-ops
    rather than constructing a client that would fall back to a hosted default, keeping the
    deterministic gate Claude-free. `ai.provider: none` (BE-0394) arrives through the same seam as a
    gap that can never close, so the switch needs no branch of its own here — only its own wording.
    Serves `_build_alert_guard` below, and through it the `crawl` / `record` path. `run` no longer
    reaches here at all: BE-0402 removed its alert guard's vision fallback.
    """
    from bajutsu.common.agents.alerts import ClaudeAlertLocator

    # The credential is provider-specific: the key named by ai.keyEnv (default ANTHROPIC_API_KEY) for
    # Anthropic, a provider-prefixed model for Bedrock (AWS credentials authenticate there), and for
    # `none` a gap that is the policy itself rather than a missing value — a key exported into the
    # shell no longer re-enables the guard. When there is a gap we don't construct the locator at all
    # and the guard no-ops rather than falling back to a hosted default. Only `record` and `crawl`
    # reach here (BE-0402), and neither has a native alert path, so each note says plainly that no
    # prompt will be cleared — the version that pointed at `run`'s native path was the right thing to
    # say only while `run` was also a caller.
    gap = credential_gap(eff.ai)
    if gap == ai_disabled.DISABLED:
        typer.echo(
            "note: alert-handling is on but the vision alert guard is off (ai.provider: none) — "
            "no screenshot is sent, and no system prompt will be cleared"
        )
    elif gap == "anthropic-key":
        typer.echo(
            f"note: alert-handling is on but ${anthropic_client.key_env(eff.ai)} is unset — "
            "the vision alert guard will no-op, so no system prompt will be cleared"
        )
    elif gap == "bedrock-model":
        typer.echo(
            "note: alert-handling is on but no Bedrock model id is set "
            "(ai.model / BAJUTSU_BEDROCK_MODEL) — the vision alert guard will no-op, "
            "so no system prompt will be cleared"
        )
    if gap is not None:
        return None
    return ClaudeAlertLocator(ai=eff.ai, redactor=redactor)


def _build_alert_guard(
    eff: Effective, redactor: Redactor, vision_instruction: str
) -> Callable[[base.Driver], AlertEvent | None] | None:
    """The bound alert-dismiss guard for a vision-only command (`crawl` / `record`), or None (BE-0260).

    Builds the shared locator (`_build_alert_locator`) and binds it to one `SystemAlertGuard` with
    the given vision instruction (an empty one falls back to the guard's built-in dismissive
    default).
    Returns None when the credential is missing, so the caller's guard simply no-ops.
    """
    from bajutsu.common.agents.alerts import SystemAlertGuard

    locator = _build_alert_locator(eff, redactor)
    if locator is None:
        return None
    return SystemAlertGuard(locator, vision_instruction or None).dismiss

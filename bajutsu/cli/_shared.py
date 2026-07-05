"""Helpers shared across CLI command modules (config loading, backend parsing).

Command-specific helpers live with their command in `commands/<name>.py`; only the
genuinely cross-command pieces belong here, so adding a command rarely edits this file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import typer
import yaml

from bajutsu import anthropic_client
from bajutsu.ai import credential_gap
from bajutsu.config import (
    WEB_ENGINES,
    Effective,
    WebConfig,
    ios_bundle_id,
    load_config,
    resolve,
)
from bajutsu.config_source import is_full_sha, materialize, parse_config_spec, source_provenance
from bajutsu.redaction import Redactor
from bajutsu.scenario import (
    Scenario,
    expand_components,
    expand_data,
    load_component,
    load_scenario_file,
    read_csv,
)

DEFAULT_CONFIG = "bajutsu.config.yaml"


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


def _parse_yaml_named[T](file: Path, parse: Callable[[str], T]) -> T:
    """Read and *parse* a YAML file, re-raising a syntax error as a `ValueError` naming *file*.

    A `yaml.YAMLError` is not a `ValueError` subclass, so a caller's `except (OSError, ValueError)`
    would leak it as a traceback. Normalizing per file — so a malformed referenced component is
    attributed to the component, not the top-level scenario — and collapsing PyYAML's multi-line
    text keeps the one-line CLI error clean and actionable (BE-0150). An `OSError` (unreadable file)
    is left to propagate unchanged.
    """
    try:
        return parse(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML in {file}: {' '.join(str(e).split())}") from e


def load_expanded_scenarios(path: Path) -> list[Scenario]:
    """Load a scenario file and expand its components + data rows, resolving refs relative to the file.

    The shared device-free loader behind `trace --explain`, `audit`, and `coverage` (setup-prefixing
    `run` keeps its own loader).

    Raises:
        OSError: The scenario file or a referenced component / CSV cannot be read.
        ValueError: The content is invalid, or the YAML does not parse — `_parse_yaml_named`
            normalizes a `yaml.YAMLError` into a `ValueError` naming the offending file (the scenario
            or a referenced component), so its callers' `except (OSError, ValueError)` guard a
            malformed file as cleanly as a structurally-invalid one (BE-0150).
    """
    base = path.parent
    scenarios = _parse_yaml_named(path, load_scenario_file).scenarios
    expand_components(scenarios, lambda ref: _parse_yaml_named(base / ref, load_component))
    return expand_data(scenarios, lambda ref: read_csv((base / ref).read_text(encoding="utf-8")))


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
        if not (d.is_dir() and manifest.is_file()):
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
    target name, and (with `require_pinned`) a Git source that isn't pinned to a commit SHA. Other
    errors — YAML parse / schema validation from ``load_config`` — propagate as exceptions.
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
        mat = materialize(spec, offline=offline)
        cfg_path, root = mat.config_path, mat.root
        source = source_provenance(spec, mat)
    # The same friendly exit-2 for a missing config, whether local or a wrong in-repo path for a
    # Git source (the materialized tree exists but doesn't hold `spec.path`).
    if not cfg_path.exists():
        typer.echo(f"config not found: {config}")
        raise typer.Exit(2)
    cfg = load_config(cfg_path.read_text(encoding="utf-8"))
    try:
        eff = resolve(cfg, target_name)
    except KeyError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    # A Git-sourced config's relative paths are relative to the checked-out tree, not the caller's
    # cwd — rebase them against the checkout root (local configs keep cwd-relative paths). A field
    # that escapes the checkout (absolute / `..`) is a clean exit-2, not a traceback.
    if root is None:
        return eff, source, None
    try:
        return eff.rebased(root), source, root
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
    """Parse a comma-separated backend string into a list, or return *fallback* when the string is empty."""
    return [b.strip() for b in backend.split(",") if b.strip()] if backend else fallback


def _resolve_browser(eff: Effective, browser: str) -> Effective:
    """Apply a `--browser` flag over the target's `browser` config (web backend, BE-0076).

    Precedence mirrors `--headed`: an explicit flag wins, else the target's config (already on
    `eff`), else the chromium default. An unknown engine is a clean exit 2 — before it reaches
    Playwright's `getattr(pw, engine)`.

    Raises:
        typer.Exit: *browser* is set but not one of the known engines (exit code 2).
    """
    if not browser:
        return eff
    if browser not in WEB_ENGINES:
        typer.echo(f"unknown --browser {browser!r}: use one of {', '.join(WEB_ENGINES)}")
        raise typer.Exit(2)
    if not isinstance(eff.platform_config, WebConfig):
        return eff  # `browser` is a web-only knob; a non-web target ignores the flag
    return replace(eff, platform_config=replace(eff.platform_config, browser=browser))


def _with_headed(eff: Effective, headed: bool | None) -> Effective:
    """Apply `--headed`/`--no-headed` to a web target's `headless` (BE-0126).

    Web-only, like `--browser`: a non-web target has no `headless` knob and ignores the flag.
    """
    if headed is None or not isinstance(eff.platform_config, WebConfig):
        return eff
    return replace(eff, platform_config=replace(eff.platform_config, headless=not headed))


def _log_subsystem_default(eff: Effective) -> str:
    """The default idb log subsystem — the iOS bundle id, or empty for a non-iOS target (BE-0126).

    The device-log predicate scopes to the app's bundle id; a web target has no such subsystem.
    """
    return ios_bundle_id(eff)

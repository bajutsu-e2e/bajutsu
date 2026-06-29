"""Helpers shared across CLI command modules (config loading, backend parsing).

Command-specific helpers live with their command in `commands/<name>.py`; only the
genuinely cross-command pieces belong here, so adding a command rarely edits this file.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import typer

from bajutsu import anthropic_client
from bajutsu.config import WEB_ENGINES, Effective, load_config, resolve
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
    return (
        f"no AI credential: set ${anthropic_client.key_env(eff.ai)} (the env var named by "
        "ai.keyEnv). Bajutsu's AI paths never fall back to a hosted default (BE-0047)."
    )


def _require_ai_credential(eff: Effective) -> None:
    """Fail closed when the resolved provider has no usable credential (BE-0047).

    Raises a clean exit-2 so an AI entry point never constructs a client that would round-trip to a
    hosted default. A no-op when a credential is present.
    """
    gap = anthropic_client.credential_gap(eff.ai)
    if gap is not None:
        typer.echo(_credential_gap_message(gap, eff))
        raise typer.Exit(2)


def load_expanded_scenarios(path: Path) -> list[Scenario]:
    """Load a scenario file and expand its components + data rows, resolving refs relative to the file.

    The shared device-free loader behind `trace --explain` and `audit` (setup-prefixing
    `run` keeps its own loader).

    Raises:
        OSError: The scenario file or a referenced component / CSV cannot be read.
        ValueError: The file parses but its content is invalid.
    """
    base = path.parent
    scenarios = load_scenario_file(path.read_text(encoding="utf-8")).scenarios
    expand_components(
        scenarios, lambda ref: load_component((base / ref).read_text(encoding="utf-8"))
    )
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
    return replace(eff, browser=browser)

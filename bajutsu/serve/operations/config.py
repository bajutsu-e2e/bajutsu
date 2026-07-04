"""Config / provider / API-key serve operations (BE-0127)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from bajutsu import ai_availability
from bajutsu.agents import AGENT_ENV
from bajutsu.anthropic_client import (
    ANTHROPIC_KEY_ENV,
    BEDROCK_MODEL_ENV,
    PROVIDER_ENV,
    PROVIDERS,
    AiConfig,
    provider,
)
from bajutsu.config import load_config, resolve
from bajutsu.config_source import materialize, parse_config_spec, source_provenance
from bajutsu.serve import jobs
from bajutsu.serve.helpers import (
    list_targets,
    mask_secret,
)
from bajutsu.serve.jobs import ServeState

_UNSAFE_ENV_VARS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "LANG",
        "TERM",
        "PWD",
        "OLDPWD",
        "LOGNAME",
        "TMPDIR",
        "DISPLAY",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
    }
)


def _valid_key_env_name(name: str) -> bool:
    """Whether *name* is a safe env-var name for an API key."""
    return bool(name) and name.isidentifier() and name not in _UNSAFE_ENV_VARS


def _active_key_env(state: jobs.ServeState) -> str:
    """The env var name the bound config's ``ai.keyEnv`` resolves to (BE-0097).

    Falls back to ``ANTHROPIC_API_KEY`` when no config is bound, the config has no ``keyEnv``,
    or the name fails validation (not an identifier, or a known system variable).
    """
    if state.config is not None:
        try:
            cfg = load_config(state.config.read_text(encoding="utf-8"))
            ai_settings = cfg.defaults.ai if cfg.defaults else None
            if ai_settings and ai_settings.key_env and _valid_key_env_name(ai_settings.key_env):
                return ai_settings.key_env
        except Exception:
            logging.getLogger(__name__).debug("cannot read ai.keyEnv from config", exc_info=True)
    return ANTHROPIC_KEY_ENV


def config_info(state: ServeState) -> tuple[Any, int]:
    return {
        "config": str(state.config) if state.config else None,
        "hasConfig": state.config is not None,
        "root": str(state.root.resolve()),
        # Whether GitHub OAuth login is available, so the login UI can offer a button (BE-0015 7b-2).
        "oauthEnabled": state.oauth is not None,
    }, 200


def api_key_info(state: ServeState, reveal: bool) -> tuple[Any, int]:
    """Whether a key is set in the serve process's environment, with a redacted preview.  ``reveal``
    adds the full value — only on explicit request, and gated by the auth check when a token is
    configured (the local backend additionally binds to localhost)."""
    key = os.environ.get(_active_key_env(state)) or None
    payload: dict[str, Any] = {"set": key is not None}
    if key is not None:
        payload["masked"] = mask_secret(key)
        if reveal:
            payload["value"] = key
    return payload, 200


def provider_info(state: ServeState) -> tuple[Any, int]:
    """The AI mode spawned jobs will use, with the Bedrock region/model.  Read from the serve
    process's environment, so it reflects what a record/crawl job inherits. `claude-code` is the
    authoring agent (BAJUTSU_AGENT) reported as a third "provider" so the Settings selector is a
    single choice; the SDK `provider()` underneath still backs the alert guard / triage."""
    mode = "claude-code" if os.environ.get(AGENT_ENV) == "claude-code" else provider()
    # Claude reachability for the resolved backend/provider (BE-0101), so the front end disables the
    # Claude tabs (record/crawl) on data rather than only surfacing the failure on click. Honors the
    # bound config's `ai.keyEnv` (BE-0097) so the SDK-path check reads the right env var.
    gap = ai_availability.from_env(os.environ, ai=AiConfig(key_env=_active_key_env(state)))
    return {
        "provider": mode,
        "region": os.environ.get("AWS_REGION", ""),
        "model": os.environ.get(BEDROCK_MODEL_ENV, ""),
        "claudeAvailable": gap is None,
        "claudeGap": gap,
        "claudeHint": ai_availability.message(gap) if gap is not None else "",
    }, 200


def _confined_config_path(root: Path, raw: str) -> Path | None:
    """Resolve *raw* (relative to *root*, or an absolute path) to a path confined to *root*, or None
    if it escapes — the one barrier between client input and a filesystem read. Resolving **first**
    normalizes any ``..`` so the containment check is sound: an absolute path left unresolved could
    keep *root* as a literal parent while the real file lies outside it (a path-traversal read)."""
    target = (Path(raw) if Path(raw).is_absolute() else root / raw).resolve()
    base = root.resolve()
    return target if (target == base or base in target.parents) else None


def bind_config(state: ServeState, raw: str) -> tuple[Any, int]:
    """Bind a config.yml chosen in the UI's file browser.  The path is confined to ``--root``; we
    validate it loads, then re-point ``state.config`` so targets/scenarios come from it."""
    if not raw:
        return {"error": "path is required"}, 400
    target = _confined_config_path(state.root, raw)
    if target is None:
        return {"error": "path is outside the browse root"}, 400
    if not target.is_file():
        return {"error": "config not found"}, 404
    try:
        load_config(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid config: {e}"}, 400
    state.release_upload()  # a fresh config replaces any bound bundle and resets cwd to serve's launch dir
    state.config = target
    state.git_config_from_api = False  # a local file config is operator-trusted (BE-0121)
    return {"ok": True, "config": str(target), "targets": list_targets(target)}, 200


def bind_git_config(state: ServeState, spec_str: str) -> tuple[Any, int]:
    """Bind a config from a Git source chosen in the UI (the "from Git" picker, BE-0063).

    *spec_str* is a `github:owner/repo@ref:path` (or `git+https://…`) string. We materialize the
    repo subtree at the ref into the content-addressed cache, validate the config loads, then point
    `state.config` at the checkout's config **and** `state.cwd` at the checkout root — so the config's
    relative `scenarios` / `appPath` / `build` resolve against the fetched tree, not serve's launch
    directory. This does not widen the file browser, which stays confined to `--root`; the checkout is
    a Bajutsu-managed cache (`materialize` refuses tar path-traversal on extraction), and each target's
    path fields are **confined to the checkout root** at bind (`Effective.rebased`) so a fetched config
    can't point serve's scenario/build logic at host paths outside the tree (BE-0063)."""
    if not spec_str:
        return {"error": "a Git config spec is required"}, 400
    spec = parse_config_spec(spec_str)
    if spec is None:
        return {
            "error": f"not a Git config spec: {spec_str!r} (use github:owner/repo@ref:path)"
        }, 400
    try:
        mat = materialize(spec)
    except (OSError, ValueError) as e:
        return {"error": f"could not fetch the Git config: {e}"}, 400
    if not mat.config_path.is_file():
        return {
            "error": f"config not found in the repository at {spec.path or 'bajutsu.config.yaml'}"
        }, 404
    try:
        cfg = load_config(mat.config_path.read_text(encoding="utf-8"))
        # Confine every target's path fields to the checkout: a fetched config that points
        # `scenarios`/`appPath`/… at an absolute or `../` path outside the tree is rejected here, so
        # serve's (unconfined) scenario/build resolution only ever sees in-checkout paths (BE-0051).
        for name in cfg.targets:
            resolve(cfg, name).rebased(mat.root)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid config: {e}"}, 400
    state.release_upload()  # switching to a Git config drops any bound bundle's sandbox
    state.config = mat.config_path
    state.cwd = mat.root  # the checkout root: the config's relative paths resolve from here
    # A Git config bound here came in over the API, not from the operator's startup flags, so its
    # `build:` command is untrusted and stays ungoverned until --allow-remote-build opts in (BE-0121).
    state.git_config_from_api = True
    return {
        "ok": True,
        "config": str(mat.config_path),
        "targets": list_targets(mat.config_path),
        "source": source_provenance(spec, mat),
    }, 200


def set_api_key(state: ServeState, value: str) -> tuple[Any, int]:
    """Set the Claude API key in the serve process's environment for this session (empty clears
    it).  Held in memory only — never written to disk — and inherited by spawned record/run jobs.
    Honours the bound config's ``ai.keyEnv`` (BE-0097)."""
    var = _active_key_env(state)
    value = value.strip()
    if value and any(c.isspace() for c in value):
        return {"error": "the API key must not contain whitespace"}, 400
    if value:
        os.environ[var] = value
        return {"ok": True, "set": True, "masked": mask_secret(value)}, 200
    os.environ.pop(var, None)
    return {"ok": True, "set": False}, 200


def set_provider(state: ServeState, body: dict[str, Any]) -> tuple[Any, int]:
    """Select the AI mode for spawned record/crawl jobs: the Anthropic API, Amazon Bedrock, or
    Claude Code (the `claude` CLI on your subscription). Written into the serve process's
    environment for this session only — never to disk — and inherited by jobs, mirroring the
    API-key handler. The first two are SDK providers (`BAJUTSU_AI_PROVIDER`); `claude-code` is an
    authoring-agent choice (`BAJUTSU_AGENT`) instead, so it leaves the SDK provider at anthropic —
    the alert guard / triage always use the SDK and fall back to a no-op when unkeyed."""
    prov = str(body.get("provider", "") or "").strip().lower()
    if prov == "claude-code":
        os.environ[AGENT_ENV] = "claude-code"
        os.environ[PROVIDER_ENV] = "anthropic"
        return {"ok": True, "provider": "claude-code"}, 200
    if prov not in PROVIDERS:
        return {"error": f"unknown provider: {prov or '(empty)'}"}, 400
    # An SDK provider implies the API authoring agent; clear any prior Claude Code selection.
    os.environ[AGENT_ENV] = "api"
    if prov == "anthropic":
        os.environ[PROVIDER_ENV] = "anthropic"
        return {"ok": True, "provider": "anthropic"}, 200
    # Bedrock needs a provider-prefixed model id (the bare Anthropic id is invalid there); region is
    # optional and falls back to AWS_REGION already in the environment.
    model = str(body.get("model", "") or "").strip()
    region = str(body.get("region", "") or "").strip()
    if not model:
        return {"error": "a Bedrock model id is required"}, 400
    if any(c.isspace() for c in model) or any(c.isspace() for c in region):
        return {"error": "region and model must not contain whitespace"}, 400
    os.environ[PROVIDER_ENV] = "bedrock"
    os.environ[BEDROCK_MODEL_ENV] = model
    if region:
        os.environ["AWS_REGION"] = region
    return {"ok": True, "provider": "bedrock", "region": region, "model": model}, 200

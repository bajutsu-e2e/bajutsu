"""Config / provider / API-key serve operations (BE-0127)."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from bajutsu import ai_availability
from bajutsu.ai import known_providers, resolved_provider
from bajutsu.anthropic_client import (
    ANT_BINARY,
    ANT_CLI_MISSING,
    ANTHROPIC_KEY_ENV,
    BEDROCK_MODEL_ENV,
    PROVIDER_ENV,
    AiConfig,
    normalize_provider,
)
from bajutsu.config import load_config, resolve
from bajutsu.config_source import materialize, parse_config_spec, source_provenance
from bajutsu.serve import jobs
from bajutsu.serve.helpers import (
    list_targets,
)
from bajutsu.serve.jobs import ServeState

# The logical name of the Claude API key in the secret store (BE-0136). One named secret today; a
# future credential (e.g. a Bedrock AWS key) reuses the same store under its own name.
AI_API_KEY_SECRET = "aiApiKey"  # noqa: S105 — a secret's logical name, not a secret value

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


def active_key_env(state: jobs.ServeState) -> str:
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


# The message returned when the file browser is used on a hosted deployment — shared by the
# browse and path-bind refusals so the UI and a hand-crafted request see the same wording (BE-0108).
FS_DISABLED_ERROR = "the file browser is disabled on a hosted server"


def config_sources(state: ServeState) -> list[str]:
    """The config sources the UI may offer for *state* (BE-0108).

    Git and upload work for any deployment; the file browser (``fs``) is a local affordance — a
    hosted user has no filesystem relationship to the host — so it is dropped when hosted.
    """
    return ["git", "upload"] if state.hosted else ["git", "upload", "fs"]


def config_info(state: ServeState) -> tuple[Any, int]:
    sources = config_sources(state)
    return {
        "config": str(state.config) if state.config else None,
        "hasConfig": state.config is not None,
        # The file browser's browse ceiling — only meaningful to the fs source, so it is withheld
        # when that source is not offered (hosted), where the absolute host path is dead information
        # and needless exposure (BE-0108).
        "root": str(state.root.resolve()) if "fs" in sources else None,
        # Whether GitHub OAuth login is available, so the login UI can offer a button (BE-0015 7b-2).
        "oauthEnabled": state.oauth is not None,
        # The config sources this deployment offers, so the UI renders only the usable ones (BE-0108).
        "configSources": sources,
    }, 200


def api_key_info(state: ServeState, actor: str | None) -> tuple[Any, int]:
    """Whether the Claude API key is set, with a masked preview — never the plaintext (BE-0136).

    Write-once: there is no ``reveal`` and no ``value`` field, for any role. A masked preview is all
    any caller ever gets back, matching how GitHub Actions Secrets disclose nothing after they are
    set. Reads from the actor's org secret store, so a hosted deployment scopes it per org."""
    masked = state.for_org(state.org_of(actor)).secrets.describe(AI_API_KEY_SECRET)
    payload: dict[str, Any] = {"set": masked is not None}
    if masked is not None:
        payload["masked"] = masked
    return payload, 200


def provider_info(state: ServeState) -> tuple[Any, int]:
    """The AI provider spawned jobs will use, with the Bedrock region/model.  Read from the serve
    process's environment, so it reflects what a record/crawl job inherits — one of the registered
    providers (`api-key` / `bedrock` / `ant` / `claude-code`). Resolved through the BE-0104 registry
    so a non-SDK provider (`claude-code`, BE-0176) is reported as itself, not clamped to the SDK
    family."""
    mode = resolved_provider()
    # Claude reachability for the resolved provider (BE-0101), so the front end disables the Claude
    # tabs (record/crawl) on data rather than only surfacing the failure on click. Honors the bound
    # config's `ai.keyEnv` (BE-0097) so the SDK-path check reads the right env var.
    gap = ai_availability.availability(ai=AiConfig(key_env=active_key_env(state)))
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
    if it escapes — the one barrier between client input and a filesystem read.

    The file browser sends the resolved *absolute* path it listed (``list_fs`` returns an absolute
    ``cwd``), so an absolute *raw* inside *root* is the normal case, not an attack. ``base / raw``
    handles both forms: pathlib drops *base* when *raw* is absolute, so this is the same shape as the
    directory browser's own ``base / sub`` — deliberately, since that form resolves the client value
    without tripping the path-injection query, whereas building ``Path(raw)`` directly does. Resolving
    **first** normalizes any ``..`` and follows symlinks so the ``is_relative_to`` guard is sound: it
    admits only paths that stay under *root* and rejects the rest. Resolution stays non-strict so a
    not-yet-existing in-root path still resolves — the caller's ``is_file`` reports it as 404 rather
    than this returning None and masking it as a misleading "outside the browse root" 400. A failure
    resolving the root or the candidate (a bad path string, a symlink loop) collapses to None too.
    """
    try:
        base = root.resolve()
        target = (base / raw).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not target.is_relative_to(base):
        return None
    return target


def bind_config(state: ServeState, raw: str) -> tuple[Any, int]:
    """Bind a config.yml chosen in the UI's file browser.  The path is confined to ``--root``; we
    validate it loads, then re-point ``state.config`` so targets/scenarios come from it."""
    if state.hosted:
        # Defense in depth (BE-0108): the file browser is removed from the hosted UI, but a
        # hand-crafted path-bind must be refused too, or hiding it would be merely cosmetic.
        return {"error": FS_DISABLED_ERROR}, 403
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


def set_api_key(state: ServeState, value: str, actor: str | None) -> tuple[Any, int]:
    """Set or replace the Claude API key (an empty *value* clears it), through the write-once secret
    store (BE-0136). The response redacts what was stored — never the plaintext. Local serve holds it
    in the process env for spawned jobs to inherit (honoring the config's ``ai.keyEnv``, BE-0097); a
    hosted deployment encrypts it per org. Overwriting rotates a key — no read-back is ever needed."""
    value = value.strip()
    if value and any(c.isspace() for c in value):
        return {"error": "the API key must not contain whitespace"}, 400
    masked = state.for_org(state.org_of(actor)).secrets.set(
        AI_API_KEY_SECRET, value, updated_by=actor
    )
    if masked is not None:
        return {"ok": True, "set": True, "masked": masked}, 200
    return {"ok": True, "set": False}, 200


def set_provider(state: ServeState, body: dict[str, Any]) -> tuple[Any, int]:
    """Select the AI provider for spawned record/crawl jobs: the Anthropic API (`api-key`), Amazon
    Bedrock, the Anthropic CLI (`ant`, a browser-based OAuth/SSO sign-in — BE-0163), or the Claude
    Code CLI (`claude-code`, the local `claude` on a Pro/Max/Console seat — BE-0176). Written into
    the serve process's environment (`BAJUTSU_AI_PROVIDER`) for this session only — never to disk —
    and inherited by jobs, mirroring the API-key handler. Validated against the BE-0104 registry, so
    every AI path (authoring, the alert guard, triage) resolves through the same seam."""
    prov = normalize_provider(str(body.get("provider", "") or ""))
    if prov not in known_providers():
        return {"error": f"unknown provider: {prov or '(empty)'}"}, 400
    if prov in ("api-key", "ant", "claude-code"):
        # `api-key` authenticates with an Anthropic API key; `ant` and `claude-code` with a CLI's
        # own credential — none takes a model/region here, so the selection is just the provider name.
        os.environ[PROVIDER_ENV] = prov
        return {"ok": True, "provider": prov}, 200
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


# The cap on the error detail surfaced to the browser: the CLI's last output line, truncated to this
# many leading characters — a one-line reason without dumping the whole transcript.
_ANT_LOGIN_ERROR_TAIL = 200


def ant_login(state: ServeState) -> tuple[Any, int]:
    """Begin an interactive `ant auth login` (the Anthropic CLI's browser-based OAuth/SSO sign-in)
    in serve's own environment, so the operator authenticates the `ant` provider from the Web UI
    instead of dropping to a terminal (BE-0175).

    Local serve only. The sign-in writes a machine-global credential (``~/.config/anthropic``) that
    every AI path on this host then shares, so a hosted / multi-tenant deployment refuses it (403) —
    signing the server into one user's Claude account is not a per-session choice. The CLI opens its
    own browser and binds its own loopback callback, so this only spawns it (detached, no interactive
    stdin) and returns immediately; the caller polls `ant_login_status` and the provider gate
    (`provider_info`) flips to reachable once the token lands.

    Returns:
        ``202`` with ``{state: "running"}`` once the CLI is spawned. A click while a previous
        sign-in is still waiting **supersedes** it (the stale process is terminated and a fresh one
        started), so an abandoned browser flow never wedges the button until the CLI's own timeout.
        ``403`` when hosted, ``400`` when the `ant` binary is absent (with the same install hint the
        availability check uses), and ``500`` when the CLI is present but fails to exec.
    """
    if state.hosted:
        return {
            "error": "SSO sign-in runs on the server and writes a shared credential, so it is "
            "available only on a local serve — sign in with `ant auth login` on the host instead."
        }, 403
    if shutil.which(ANT_BINARY) is None:
        return {"error": ai_availability.message(ANT_CLI_MISSING)}, 400
    # serve is a ThreadingHTTPServer, so hold the lock across check-terminate-spawn: two concurrent
    # POSTs must not both observe no in-flight process and each spawn a CLI (the second overwrite would
    # leak the first, unsupersedable, subprocess).
    with state.ant_login_lock:
        proc = state.ant_login_proc
        if proc is not None and proc.poll() is None:
            # A previous sign-in is still waiting on its browser callback (the operator abandoned it,
            # or is deliberately restarting). Superseding it — rather than refusing — is what lets a
            # stuck attempt be retried at once instead of blocking the button until the CLI's ~5-min
            # timeout. `ant auth login` binds an ephemeral callback port, so the fresh spawn never
            # collides with the one being torn down.
            with contextlib.suppress(OSError):  # already gone between the poll and the terminate
                proc.terminate()
        try:
            # stdin=DEVNULL: `ant auth login` (default mode) races a browser loopback callback against
            # a pasted code on stdin; closing stdin makes the paste path see EOF (the CLI treats that
            # as "not a race result") so only the browser+callback flow drives it. stderr→stdout
            # merges the CLI's progress/URL lines into one stream we can tail for an error message.
            state.ant_login_proc = state.popen(
                [ANT_BINARY, "auth", "login"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as e:  # exec failure despite `which` finding the binary
            return {"error": f"could not start `ant auth login`: {e}"}, 500
    return {"ok": True, "started": True, "state": "running"}, 202


def ant_login_status(state: ServeState) -> tuple[Any, int]:
    """Poll the `ant auth login` started by `ant_login` (BE-0175).

    Returns ``{state: ...}`` — ``idle`` (none started), ``running`` (browser sign-in in progress),
    ``ok`` (the CLI exited 0; the credential is written and the provider gate now reads reachable),
    or ``error`` with a one-line ``detail`` (the CLI's last output line) when it exited non-zero
    (sign-in cancelled, timed out, or failed).
    """
    proc = state.ant_login_proc
    if proc is None:
        return {"state": "idle"}, 200
    code = proc.poll()
    if code is None:
        return {"state": "running"}, 200
    if code == 0:
        return {"state": "ok"}, 200
    detail = f"`ant auth login` exited with code {code}"
    try:
        # The process has exited, so the merged stdout buffer reads without blocking; its last
        # non-empty line is the CLI's most specific message (e.g. a timeout or "authorization denied").
        out = (proc.stdout.read() if proc.stdout else "") or ""
        if out.strip():
            detail = out.strip().splitlines()[-1][:_ANT_LOGIN_ERROR_TAIL]
    except (OSError, ValueError):  # pipe already closed / consumed by an earlier poll
        pass
    return {"state": "error", "detail": detail}, 200

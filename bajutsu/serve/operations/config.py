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

from bajutsu import __version__
from bajutsu.backends import IMPLEMENTED
from bajutsu.common import _yaml
from bajutsu.common.agents import availability as ai_availability
from bajutsu.common.agents.ai_config import (
    BEDROCK_MODEL_ENV,
    DEFAULT_LANGUAGE,
    EFFORT_ENV,
    EFFORT_LEVELS,
    LANGUAGE_ENV,
    LANGUAGES,
    MODEL_ENV,
    PROVIDER_ENV,
    AiConfig,
    normalize_provider,
)
from bajutsu.common.agents.anthropic_client import ANT_BINARY, ANT_CLI_MISSING, ANTHROPIC_KEY_ENV
from bajutsu.common.ai import credential_gap, resolved_provider, selectable_providers
from bajutsu.common.ai.registry import DISABLED_PROVIDER
from bajutsu.config import load_config, resolve, xcuitest_pins_runner
from bajutsu.config_source import materialize, parse_config_spec, source_provenance
from bajutsu.platform_lifecycle.environments import (
    bundled_products_dir,
    bundled_runner_build_info,
)
from bajutsu.serve import oplog
from bajutsu.serve.authz import forbidden_for_role
from bajutsu.serve.helpers import (
    list_targets,
    load_serve_config_file,
)
from bajutsu.serve.operations.orgs import NO_ORG_STORE_ERROR
from bajutsu.serve.orgs import (
    DEFAULT_ORG,
    orgs_declaring_membership,
    seed_orgs_from_config,
)
from bajutsu.serve.provider_store import ProviderSettingsError
from bajutsu.serve.state import OrgProviderSettings, ProviderSettings, ServeState

# The logical name of the Claude API key in the secret store (BE-0136). The store holds each named
# credential under its own name; the second one below is the `claude-code` provider's OAuth token.
AI_API_KEY_SECRET = "aiApiKey"  # noqa: S105 — a secret's logical name, not a secret value

# The logical name of the `claude-code` provider's OAuth token in the same secret store (BE-0215) —
# the second named secret BE-0136's generalization anticipated, reusing the store and its write-once
# guarantee with no new plumbing. Local serve materializes it into `CLAUDE_CODE_OAUTH_TOKEN` (via
# `ServeState._env_var_for_secret`); the hosted backend encrypts it per org like every named secret.
AI_CLAUDE_CODE_TOKEN_SECRET = "aiClaudeCodeOauthToken"  # noqa: S105 — a logical name, not a value

# The logical name of the GitHub credential for a private-repo config source (BE-0224) — a third
# named secret reusing the same write-once store. Local serve materializes it into the bajutsu-owned
# `BAJUTSU_GIT_CONFIG_TOKEN` (via `ServeState._env_var_for_secret`), which the in-process
# `bind_git_config` fetch then reads. The hosted backend encrypts it per org, so each tenant's stored
# credential is its own; wiring the per-org value into the hosted control plane's in-process fetch is
# a follow-up (the write-once store discloses no plaintext to a handler), so on hosted a private bind
# resolves through the process-global App / env credential today.
GIT_CONFIG_TOKEN_SECRET = "gitConfigToken"  # noqa: S105 — a logical name, not a value

# The three logical names above are all valid identifiers, so `_valid_key_env_name` alone would let a
# scenario-declared secret collide with one of them: `_env_var_for_secret` maps `AI_API_KEY_SECRET` to
# `active_key_env`'s env var (the operator's real Anthropic API key, or a configured `ai.keyEnv`), so
# a config that declares e.g. `secrets: [aiApiKey]` would let `GET /api/secrets` disclose that key's
# masked preview to any actor and `POST /api/secrets` overwrite it — the same collision applies to the
# other two. `declared_secret_names` excludes all three so a scenario secret can never alias a reserved
# operator credential.
_RESERVED_SECRET_NAMES = frozenset(
    {AI_API_KEY_SECRET, AI_CLAUDE_CODE_TOKEN_SECRET, GIT_CONFIG_TOKEN_SECRET}
)

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


def active_key_env(state: ServeState) -> str:
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


# Why a hosted deployment cannot capture: a capture session drives a device from the serve process
# itself, and hosted the devices sit on the workers a browser never talks to. Every
# `/api/capture/*` route is `local_only` in consequence, so the mode used to be offered and then
# 404 on its first call (#1721).
CAPTURE_HOSTED_REASON = (
    "capture drives a device from the serve process itself, which a hosted deployment has none of "
    "— capture from a local serve instead"
)

# Why a local deployment that nonetheless serves no `local_only` route cannot capture: `serve
# --asgi` runs the FastAPI transport with the local backend, and that transport registers no
# `local_only` route (`serve.server.app.make_app`). The devices are right there, so the reason names
# the transport rather than the deployment.
CAPTURE_NO_LOCAL_ROUTES_REASON = "this server does not serve the capture routes — run `bajutsu serve` without `--asgi` to capture"

# Why a caller cannot administer orgs on a deployment that does have a database: the roster
# discloses one tenant's membership to another, so every `/api/orgs` verb is admin-only.
ORGS_ADMIN_ONLY_REASON = "org management is admin-only"


def _capability(reason: str | None) -> dict[str, Any]:
    """One capability entry: available when nothing blocks it, else the reason a disabled control
    shows. Always both fields, so the UI reads one shape whichever way the answer falls."""
    return {"available": reason is None, "reason": reason}


def _capture_unavailable(state: ServeState) -> str | None:
    """Why capture is unavailable here, or None when it is available.

    Keyed to whether the transport serves the `local_only` routes, not to `hosted`: both hosted and
    a local `serve --asgi` go through `make_app` and so serve none of them. Hosted is reported
    first, since there the devices really are elsewhere and the transport is beside the point.
    """
    if state.hosted:
        return CAPTURE_HOSTED_REASON
    if not state.serves_local_routes:
        return CAPTURE_NO_LOCAL_ROUTES_REASON
    return None


def _orgs_unavailable(state: ServeState, actor: str | None) -> str | None:
    """Why *actor* cannot administer orgs here, or None when they can.

    `forbidden_for_role` is the same gate the transport applies to `GET /api/orgs`, so the flag and
    the endpoint cannot disagree. It gates an OAuth-authenticated session: without an identity (a
    local serve, a shared-token session) there is no role to fail, exactly as the transport has it.
    """
    if state.repository is None:
        return NO_ORG_STORE_ERROR
    if actor and forbidden_for_role(state, actor, "GET", "/api/orgs"):
        return ORGS_ADMIN_ONLY_REASON
    return None


def serve_capabilities(state: ServeState, actor: str | None = None) -> dict[str, Any]:
    """What this deployment can serve *this* caller, for the UI to gate its own surface on (#1721).

    Offering a mode whose every call 404s (Capture on a hosted serve) or probing an endpoint that
    can only answer 400 (the org roster without a database) spends the user's attention on a
    failure the server already knew about. Reporting availability up front lets the UI disable the
    one and skip the other — and each reason is carried alongside, since a control the user cannot
    use should say why rather than just going grey.

    Purely a report: every endpoint still refuses the call on its own, so a flag read at boot and
    since gone stale widens no gate.
    """
    return {
        "capture": _capability(_capture_unavailable(state)),
        "orgs": _capability(_orgs_unavailable(state, actor)),
    }


def config_info(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    """The boot read every tab starts from: what config is bound, what this deployment offers, and
    who the caller is (BE-0375).

    *actor* is the caller's own GitHub login, so returning it and the org it resolves to discloses
    nothing they did not already present — unlike the org *roster*, which stays behind the admin-only
    `GET /api/orgs`. Both are None on a deployment with no signed-in identity (local serve, a
    shared-token session), where `org_of` would answer `default` for everyone and a header badge
    saying so would be noise rather than information.
    """
    sources = config_sources(state)
    return {
        "config": str(state.config) if state.config else None,
        "hasConfig": state.config is not None,
        # The file browser's browse ceiling — only meaningful to the fs source, so it is withheld
        # when that source is not offered (hosted), where the absolute host path is dead information
        # and needless exposure (BE-0108).
        "root": str(state.root.resolve()) if "fs" in sources else None,
        # Whether GitHub OAuth login is available, so the login UI can offer a button (BE-0015 7b-2).
        "oauthEnabled": state.auth.oauth is not None,
        # The config sources this deployment offers, so the UI renders only the usable ones (BE-0108).
        "configSources": sources,
        # The soft-delete retention window in days, so the delete confirm + Trash view can state how
        # long a trashed run stays restorable (BE-0239). <= 0 means retention is disabled — trash is
        # kept until a manual purge.
        "retentionDays": state.run_retention_days,
        # Who this request is, and which tenant it acts as (BE-0375). The header shows the pair so an
        # admin managing several orgs can see which one their own runs, secrets, and evidence land
        # in — the one org whose scope every other tab silently applies.
        "actor": actor,
        "org": state.org_of(actor) if actor else None,
        # The orgs this caller may switch between, so the header can offer the choice. Their own
        # memberships, disclosed to them alone — nothing they could not learn by switching, which is
        # why the full roster stays behind the admin-only `GET /api/orgs`. One entry (or none) means
        # there is nothing to choose and the header keeps the read-only badge.
        "orgs": sorted(state.eligible_orgs(actor)) if actor else [],
        # What this deployment can actually serve, so the UI disables Capture where the routes are
        # not served and asks for the org roster only where there is one to ask for (#1721).
        "capabilities": serve_capabilities(state, actor),
    }, 200


def _json_safe(value: Any) -> Any:
    """Coerce a parsed-YAML structure into JSON-serializable types for the config-content payload.

    The YAML loader can emit scalars JSON has no type for (a `date`/`datetime` from a timestamp,
    say); left as-is they would make the stdlib handler's `json.dumps` raise. Recurse containers and
    stringify any leaf that is not already a JSON scalar; dict keys are stringified too (JSON keys
    are strings). Only affects the display structure — the raw YAML text is returned untouched.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def config_content(state: ServeState) -> tuple[Any, int]:
    """The raw text of the active config plus its source, so the UI can confirm *what* is bound.

    The path in `config_info` is enough for a local file, but a Git-sourced config resolves to an
    opaque content-addressed cache path (`…/gitsrc/<host>/<owner>/<repo>/<sha>/…`); this returns the
    YAML the tabs actually run from and — for a Git source — the `provenance` stamp (host/owner/repo/
    ref/resolved sha) so the reader sees which commit it came from, not just the path.

    The text is verbatim: any ``${secrets.*}`` placeholders are shown as written, never resolved, so
    this discloses nothing beyond the file already committed to Git or uploaded in the bundle.
    """
    if state.config is None:
        return {"error": "no config bound"}, 404
    try:
        content = state.config.read_text(encoding="utf-8")
    except OSError as e:
        # The bound path was validated at bind time; a read failure here means it moved/was removed
        # under us (a transient checkout, a deleted file) — report it rather than 500 with a traceback.
        return {"error": f"could not read config: {e}"}, 404
    # The parsed structure powers the UI's collapsible key/value view. Use the same restricted loader
    # `load_config` uses (`_yaml`), not `yaml.safe_load`: YAML 1.1 implicit typing would turn an `on:`
    # trigger key into `True`, so a plain load would misrender a valid config. `_json_safe` then coerces
    # any non-JSON scalar the loader can still emit (e.g. a `date`) to a string, so the payload is always
    # JSON-serializable — the stdlib handler dumps it directly and would 500 on a raw `date`. A parse
    # failure leaves `parsed` null and the UI falls back to the raw YAML rather than erroring.
    try:
        parsed = _json_safe(_yaml.safe_load(content))
    except yaml.YAMLError:
        parsed = None
    return {
        "config": str(state.config),
        "content": content,
        "parsed": parsed,
        "provenance": state.config_provenance,  # None for a local file / uploaded bundle
    }, 200


def _config_overrides_ios_runner(state: ServeState) -> bool:
    """Whether the bound config names an explicit ``xcuitest.testRunner`` on any iOS target (BE-0318).

    A config can carry several iOS targets; the server-wide readiness line reports "overridden" when
    *any* of them sets a runner, since such a target never falls back to the bundled one. No config
    bound, no iOS target, or a config that fails to load reads as no override (the tab then shows the
    bundled-runner state unqualified); a load failure is logged at debug, matching ``active_key_env``.

    Each target's ``resolve`` is guarded on its own: one unresolvable target must not mask a pinned
    runner on another (the row is the tab's sharpest signal), so a per-target failure is logged and
    skipped rather than collapsing the whole answer to ``False``.
    """
    if state.config is None:
        return False
    try:
        cfg = load_config(state.config.read_text(encoding="utf-8"))
    except Exception:
        logging.getLogger(__name__).debug(
            "cannot load config to check the xcuitest.testRunner override", exc_info=True
        )
        return False
    for name in cfg.targets:
        try:
            if xcuitest_pins_runner(resolve(cfg, name)):
                return True
        except Exception:
            logging.getLogger(__name__).debug(
                "cannot resolve target %r to check the xcuitest.testRunner override",
                name,
                exc_info=True,
            )
    return False


def _ios_runner_status(state: ServeState) -> dict[str, Any]:
    """The bundled iOS XCUITest runner's deployment state, for the Server settings tab (BE-0318).

    Answers "will an iOS Simulator run that names no runner find one, and what was it built against?"
    without starting a run — the one filesystem probe this tab makes (BE-0292's ``_bundled_runner``,
    re-exported at the ``environments`` package root). App-agnostic and server-wide, so it lives here
    rather than per-target.

    Fields: ``bundled`` (this build ships the generic runner as package data — the fallback a
    runner-less Simulator run resolves to), ``buildInfo`` (the ``{"xcode", "sdk"}`` toolchain the
    runner was built against, or ``None`` when no bundle / no metadata), and ``override`` (the bound
    config names an explicit ``xcuitest.testRunner``, so the bundle isn't the runner that would run).
    """
    return {
        "bundled": bundled_products_dir() is not None,
        "buildInfo": bundled_runner_build_info(),
        "override": _config_overrides_ios_runner(state),
    }


def server_settings(state: ServeState) -> tuple[Any, int]:
    """The running server's resolved configuration, read-only, for the Server settings tab (BE-0318).

    A Tier-1, AI-free view assembled from the already-resolved ``ServeState`` plus one filesystem
    probe (the bundled iOS runner): nothing here is a per-run signal, so nothing reaches the
    deterministic ``run`` / CI verdict (prime directive 1).

    Authz matches ``config_info`` — open to any viewer — so it discloses only what that posture
    permits. Two consequences: the admin-gated commit/branch of ``server_checkout`` is **not**
    surfaced (a branch name can leak an in-progress work topic), and — stricter than ``config_info``,
    which returns its ``config`` path unconditionally — the host paths (``config``, ``runsDir``,
    ``baselinesDir``) are withheld when ``state.hosted``, since a hosted user has no filesystem
    relationship to the host and an absolute host path is then dead information (BE-0108). The
    non-path fields (deployment mode, backends, retention window, concurrency caps, version, the
    config's Git source, and the iOS runner state) are shown either way; the endpoint reports presence
    and configuration only, never a secret's plaintext.
    """
    payload: dict[str, Any] = {
        # The one deployment-posture field the UI displays; the host-path redaction below keys off
        # `state.hosted` directly, so a second boolean here would only be a redundant encoding of it.
        "mode": "hosted" if state.hosted else "local",
        "version": __version__,
        "hasConfig": state.config is not None,
        # The config's Git source (host/owner/repo/ref/sha), or None for a local file / uploaded
        # bundle — the "where the bound config came from" the version row's commit deliberately isn't.
        "configSource": state.config_provenance,
        # The backends this build has a driver for (fake / playwright / xcuitest / adb) — a static
        # server-wide fact, sorted for a stable display; not a per-backend availability probe (the
        # tab makes exactly one filesystem probe, the iOS runner below).
        "backends": sorted(IMPLEMENTED),
        # The soft-delete retention window in days (BE-0239); <= 0 means the automatic purge is off.
        "retentionDays": state.run_retention_days,
        # The concurrency caps (BE-0051 / BE-0015 / BE-0016); <= 0 means unlimited for that axis.
        "concurrency": {
            "total": state.max_concurrent,
            "perUser": state.max_concurrent_per_user,
            "perOrg": state.max_concurrent_per_org,
        },
        "iosRunner": _ios_runner_status(state),
    }
    if not state.hosted:
        # Host filesystem paths: meaningful only on a local deployment (BE-0108), so withheld hosted.
        payload["config"] = str(state.config) if state.config else None
        payload["runsDir"] = str(state.runs_dir)
        payload["baselinesDir"] = str(state.baselines_dir)
    return payload, 200


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


def claude_code_token_info(state: ServeState, actor: str | None) -> tuple[Any, int]:
    """Whether the `claude-code` OAuth token is set, with a masked preview — never plaintext (BE-0215).

    The write-once counterpart to `api_key_info` for the second named secret: same shape, same store,
    read from the actor's org so a hosted deployment scopes it per org. No `reveal`, no `value` field."""
    masked = state.for_org(state.org_of(actor)).secrets.describe(AI_CLAUDE_CODE_TOKEN_SECRET)
    payload: dict[str, Any] = {"set": masked is not None}
    if masked is not None:
        payload["masked"] = masked
    return payload, 200


def _load_org_settings(state: ServeState, org: str) -> OrgProviderSettings:
    """Load *org*'s persisted AI provider selection from its store, validated (BE-0229).

    The value shape of the boot restore, generalized per org. With no store wired, no file/row saved
    yet, or a malformed/inconsistent one, returns an empty selection — resolution then falls back to
    the launch env / defaults, keeping the zero-config path (BE-0101) unchanged. A file that exists
    but is malformed or names an unknown/invalid active provider is logged loudly and dropped rather
    than materializing an invalid choice that only fails at the first AI call (determinism-first:
    loud, not silent). The language (BE-0188) is not persisted, so a freshly loaded selection has
    none until a save sets it."""
    store = state.for_org(org).provider_settings
    if store is None:
        return OrgProviderSettings()
    try:
        data = store.load()
    except ProviderSettingsError:
        logging.getLogger(__name__).warning(
            "ignoring the persisted AI provider settings: the file is malformed; "
            "falling back to the environment defaults",
            exc_info=True,
        )
        return OrgProviderSettings()
    if data is None:
        return OrgProviderSettings()
    active = data.settings.get(data.provider)
    if (
        active is None
        or data.provider not in selectable_providers()
        or not _valid_slot(data.provider, active)
    ):
        # A well-formed save always records a selectable active provider with valid values. A file
        # that names an unselectable provider (unregistered, or the kill switch `none` the dropdown
        # never offers — BE-0394), an active provider with no slot, a bedrock slot with a blank
        # model, or any slot with an invalid effort/model/region is hand-edited or corrupt.
        # Seeding from it would materialize an invalid choice that only fails at the first AI call, so
        # fall back to the env defaults here instead — loud, not silent.
        logging.getLogger(__name__).warning(
            "ignoring the persisted AI provider settings: the active provider %r is unknown or "
            "unselectable, missing, or has an invalid saved slot; falling back to the "
            "environment defaults",
            data.provider,
        )
        return OrgProviderSettings()
    known = selectable_providers()
    slots: dict[str, ProviderSettings] = {}
    for name, settings in data.settings.items():
        if name not in known:
            # A stale/hand-edited file can carry a slot for a provider that no longer exists — or one
            # registered but not selectable (`none`, BE-0394); skip it rather than surfacing a bogus
            # entry in the Settings UI. Loud, like the active guard.
            logging.getLogger(__name__).warning(
                "ignoring a persisted settings slot for the unselectable provider %r", name
            )
            continue
        if not _valid_slot(name, settings):
            # A structurally valid (all-string) but semantically invalid slot — an unrecognised
            # effort or a model/region with whitespace, the same rules set_provider enforces on
            # write. Skip it rather than resolve from it — loud, not silent.
            logging.getLogger(__name__).warning(
                "ignoring a persisted settings slot for %r: invalid effort or model/region", name
            )
            continue
        slots[name] = settings
    return OrgProviderSettings(provider=data.provider, slots=slots)


def _org_settings(state: ServeState, org: str) -> OrgProviderSettings:
    """*org*'s in-memory AI provider selection, loaded from its store on first access (BE-0229).

    The per-org read path every AI-resolving surface goes through. Once loaded (even to an empty
    selection) the org's entry stays in memory, so a malformed store is logged once, not on every
    request. Local serve's single `default` org makes this exactly one entry."""
    existing = state.providers.org_provider_settings(org)
    if existing is not None:
        return existing
    loaded = _load_org_settings(state, org)
    state.providers.put_org_provider_settings(org, loaded)
    return loaded


def _active_slot(settings: OrgProviderSettings, mode: str) -> ProviderSettings:
    """The slot the Settings UI shows (and reachability checks) for the active provider *mode*: the
    org's saved slot, or — when the org set nothing for it — the launch-env value, so a value set
    before serve started (or via env alone) still shows (BE-0183 / the zero-config path)."""
    saved = settings.slots.get(mode)
    if saved is not None:
        return saved
    return ProviderSettings(
        model=os.environ.get(BEDROCK_MODEL_ENV if mode == "bedrock" else MODEL_ENV, ""),
        effort=os.environ.get(EFFORT_ENV, ""),
        region=os.environ.get("AWS_REGION", "") if mode == "bedrock" else "",
    )


def _provider_settings_map(
    settings: OrgProviderSettings, mode: str, active_slot: ProviderSettings
) -> dict[str, dict[str, str]]:
    """The per-provider settings the Settings UI pre-populates from (BE-0183): every provider the org
    has a remembered slot for, plus the active provider's resolved *active_slot*. *mode* and
    *active_slot* are resolved once by the caller so a concurrent save can't flip them mid-read."""
    slots = dict(settings.slots)
    slots[mode] = active_slot
    return {
        name: {"model": s.model, "effort": s.effort, "region": s.region}
        for name, s in slots.items()
    }


def provider_env(settings: OrgProviderSettings) -> dict[str, str]:
    """The env-var overlay a spawned job needs to resolve *settings*' active provider (BE-0229).

    The per-org counterpart to the old process-global `_apply_provider_env`: it returns a dict merged
    onto the spawn's env by `_spawn_env`, never mutating `os.environ`, so one org's selection can't
    leak into another org's jobs. Empty when no provider is selected — the zero-config path (BE-0101)
    then falls back to the job's inherited env unchanged. Only values that are set are emitted; a
    blank model/effort/language is simply absent (the spawn strips the managed vars first, so absence
    resolves to the default rather than inheriting a stale launch value)."""
    prov = settings.provider
    if not prov:
        return {}
    env = {PROVIDER_ENV: prov}
    slot = settings.slots.get(prov, ProviderSettings())
    if slot.effort:
        env[EFFORT_ENV] = slot.effort
    if prov == "bedrock":
        # Bedrock takes a provider-prefixed model id in its own slot; a region is optional.
        env[BEDROCK_MODEL_ENV] = slot.model
        if slot.region:
            env["AWS_REGION"] = slot.region
    elif slot.model:
        # api-key / ant / claude-code take a bare Anthropic id via the general override (blank = default).
        env[MODEL_ENV] = slot.model
    if settings.language:
        env[LANGUAGE_ENV] = settings.language
    return env


def resolve_provider_env(state: ServeState, org: str) -> dict[str, str]:
    """The AI provider env overlay for a job started by *org* (BE-0229) — the seam `_register_and_
    dispatch` attaches to every job so the spawn uses that org's saved selection. Loads the org's
    settings (from its store on first access) and materializes them; empty when nothing is selected."""
    return provider_env(_org_settings(state, org))


def provider_info(state: ServeState, actor: str | None) -> tuple[Any, int]:
    """The AI provider spawned jobs will use for the actor's org, with the Bedrock region/model.
    Resolved from that org's saved selection (BE-0229) rather than the shared process env, so a
    hosted deployment reports each org its own choice — one of the registered providers (`api-key` /
    `bedrock` / `ant` / `claude-code`). Resolved through the BE-0104 registry so a non-SDK provider
    (`claude-code`, BE-0176) is reported as itself. `providers` carries every provider's own
    remembered model/effort/region (BE-0183) so the Settings UI can swap fields on a dropdown change
    without a round trip."""
    settings = _org_settings(state, state.org_of(actor))
    # The active provider is the org's saved choice, else the launch-env / registry default.
    mode = settings.provider or resolved_provider()
    active_slot = _active_slot(settings, mode)
    # Claude reachability for the resolved provider (BE-0101), so the front end disables the Claude
    # tabs (record/crawl) on data rather than only surfacing the failure on click. The org's provider
    # + its model drive the check (so a bedrock selection reads reachable once it has a model); the
    # key check still reads `os.environ` via `ai.keyEnv` (BE-0097) — the pre-existing local-key path.
    gap = credential_gap(
        AiConfig(provider=mode, model=active_slot.model or None, key_env=active_key_env(state))
    )
    return {
        "provider": mode,
        "region": active_slot.region if mode == "bedrock" else "",
        "model": active_slot.model if mode == "bedrock" else "",
        "aiModel": active_slot.model if mode != "bedrock" else "",  # non-Bedrock model override
        "effort": active_slot.effort,
        "language": settings.language,  # AI output language (BE-0188)
        "providers": _provider_settings_map(settings, mode, active_slot),
        "claudeAvailable": gap is None,
        "claudeGap": gap,
        "claudeHint": ai_availability.message(gap) if gap is not None else "",
    }, 200


def _confined_config_path(root: Path, raw: str) -> Path | None:
    """Resolve *raw* (relative to *root*, or an absolute path) to a path confined to *root*, or None
    if it escapes — the one barrier between client input and a filesystem read.

    Accepts both a relative path (resolved under *root*) and an absolute path: the file browser posts
    the absolute paths ``/api/fs`` returns, so rejecting them outright would break every valid in-root
    selection. ``base / raw`` handles both — pathlib drops *base* when *raw* is absolute — and is the
    same join the directory browser's ``base / sub`` uses; that shape resolves the client value
    without tripping the path-injection query, whereas building ``Path(raw)`` directly does. Resolving
    **first** normalizes any ``..`` and follows symlinks so the containment check is sound; the
    explicit ``is_relative_to`` guard (not a suppressed ``relative_to``) is what CodeQL recognizes as
    the barrier, so the downstream ``is_file`` / ``read_text`` sinks stay clear. Resolution is
    non-strict so a not-yet-existing in-root path still resolves — the caller's ``is_file`` reports it
    as 404 rather than this masking it as a misleading "outside the browse root" 400. A NUL byte or a
    resolution failure (bad path string, symlink loop) collapses to None."""
    if not raw or not raw.strip() or "\x00" in raw:
        return None
    try:
        base = root.resolve(strict=False)
        target = (base / raw.strip()).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if not target.is_relative_to(base):
        return None
    return target


def seed_orgs_from_bound_config(state: ServeState) -> None:
    """Seed the launch config's `orgs:` block into an empty `orgs` table, once (BE-0375).

    Called from `serve()` at startup and from nowhere else. Deliberately *not* from the API binds
    (`bind_config`, `bind_git_config`, the upload and compose binds): those accept a configuration
    whose content the deployment does not own — BE-0121 says as much of the same file's `build:`,
    which stays ungoverned until `--allow-remote-build` opts in — and seeding from one would hand
    that file authority over who may sign in. Before this item that authority existed but was live
    and reversible, because sign-in re-read the file every time; a seeded row outlives the bind, so
    rebinding away would no longer revoke it. An org first declared through an API bind is therefore
    created on the Orgs page instead.

    Seeds only when the table holds no org at all, deleted ones included. One boot converts a
    configuration-only deployment; from then on the database is the sole author of its own roster,
    and no later configuration edit — nor a restart carrying one — can add, revive, or reshape a
    tenant behind an admin's back. A no-op without a repository (a database-less deployment keeps
    reading `orgs:` directly) or without a loadable config.

    A repository failure is reported and swallowed rather than propagated: a bad database read must
    not stop a server from booting, and the next start re-runs this. It is not thereby hidden — it
    is logged at WARNING here, and a database `serve` cannot read independently turns sign-in into a
    5xx that names the store (`oauth_callback`), never a silent denial.
    """
    if state.repository is None:
        return
    parsed = load_serve_config_file(state.config)
    if parsed is None:
        return
    try:
        if state.repository.list_orgs(include_deleted=True):
            # Past the conversion: the database has a roster of its own and config no longer writes
            # it, so every entry still declaring membership is one an operator should hear about.
            # Reported rather than skipped silently — the warning is the only signal that a field
            # they are still editing stopped being read.
            ignored = orgs_declaring_membership(parsed[1])
        else:
            ignored = seed_orgs_from_config(state.repository, parsed[1])
    except Exception as exc:
        oplog.log_event(
            logging.getLogger(__name__),
            "org.seed.failed",
            f"could not seed org membership from the launch config ({type(exc).__name__}); "
            "it will be retried at the next startup",
            level=logging.WARNING,
            check="orgs_seed_failed",
        )
        return
    if ignored:
        # An `orgs:` entry that carries only `targets` stays legitimate — target ownership is still
        # config's (BE-0375 unit 1) — so this fires only on an entry still declaring membership
        # fields the database has already taken over. The same "an operator forgot this is no longer
        # read" signal BE-0352 gives for its own retired environment variable.
        oplog.log_event(
            logging.getLogger(__name__),
            "org.membership.ignored",
            "these orgs: entries still declare members/githubOrgs/githubTeams/editorTeams, which "
            "the database "
            f"now owns and this deployment no longer reads: {sorted(ignored)} — pare each entry "
            "down to targets: only, and edit membership on the Orgs page instead (an org already "
            "retired there is gone from the page, so paring its entry is all that is left to do)",
            level=logging.WARNING,
            check="orgs_membership_ignored",
        )


def bind_config(state: ServeState, raw: str) -> tuple[Any, int]:
    """Bind a config.yml chosen in the UI's file browser.  The path is confined to ``--root``; we
    validate it loads and its path fields stay within ``--root`` too, then re-point ``state.config``
    at it **and** ``state.cwd`` at its own directory so the config's relative paths resolve from
    beside it, not serve's launch dir (BE-0242) — mirroring the Git/upload binds."""
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
        cfg = load_config(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid config: {e}"}, 400
    # Validate path fields stay within `--root` (BE-0051): resolved against the config's own
    # directory (matching `state.cwd` below), but confined to the broader browse root rather than
    # that one directory, so an in-root sibling reference (`../scenarios` from a config nested under
    # `<root>/configs/`) still resolves — only an escape past `--root` itself is refused.
    config_dir = target.resolve().parent
    try:
        for name in cfg.targets:
            resolve(cfg, name).rebased(config_dir, confine=True, confine_to=state.root)
    except ValueError as e:
        return {"error": f"config path validation failed: {e}"}, 400
    state.release_upload()  # a fresh config replaces any bound bundle and resets cwd to serve's launch dir
    state.config = target
    state.cwd = config_dir  # the config's relative paths resolve from its own directory (BE-0242)
    state.config_provenance = None  # a local file has no Git commit provenance to show
    # A local file's *build:* stays trusted even when bound through this API endpoint (BE-0121), unlike
    # a Git spec or an upload: this endpoint can only bind a file `_confined_config_path` already found
    # inside `--root`, so the operator (who chose what lives under `--root`) already controls every
    # candidate's content — there is no path by which this bind hands an attacker-authored `build:` to
    # the host, the exact capability BE-0121 gates behind `--allow-remote-build`. The `confine=True`
    # above is a separate, narrower guard: it stops an in-`--root` config's own path *fields* from
    # resolving outside the directory its relative paths are defined against, regardless of who bound
    # it — orthogonal to, not a relaxation of, this build-trust call.
    state.git_config_from_api = False
    return {"ok": True, "config": str(target), "targets": list_targets(target)}, 200


def bind_git_config(
    state: ServeState, spec_str: str, *, actor: str | None = None
) -> tuple[Any, int]:
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
            resolve(cfg, name).rebased(mat.root, confine=True)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid config: {e}"}, 400
    state.release_upload()  # switching to a Git config drops any bound bundle's sandbox
    state.config = mat.config_path
    state.cwd = mat.root  # the checkout root: the config's relative paths resolve from here
    provenance = source_provenance(spec, mat)
    state.config_provenance = provenance  # so /api/config/content can show the resolved commit
    # A Git config bound here came in over the API, not from the operator's startup flags, so its
    # `build:` command is untrusted and stays ungoverned until --allow-remote-build opts in (BE-0121).
    state.git_config_from_api = True
    # A Git-sourced config is bound *as* the acting org, and its content is not this deployment's
    # (the `build:` trust note above says as much), so that org owns every target it declares and
    # its `orgs:` block partitions nothing (BE-0375).
    state.config_org = state.org_of(actor)
    return {
        "ok": True,
        "config": str(mat.config_path),
        "targets": list_targets(mat.config_path),
        "source": provenance,
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


def set_claude_code_token(state: ServeState, value: str, actor: str | None) -> tuple[Any, int]:
    """Set or replace the `claude-code` OAuth token (an empty *value* clears it), through the same
    write-once secret store (BE-0215). The response redacts what was stored — never the plaintext.
    Local serve holds it in ``CLAUDE_CODE_OAUTH_TOKEN`` for spawned jobs to inherit; a hosted
    deployment encrypts it per org. Mirrors `set_api_key`; overwriting rotates the token."""
    value = value.strip()
    if value and any(c.isspace() for c in value):
        return {"error": "the OAuth token must not contain whitespace"}, 400
    masked = state.for_org(state.org_of(actor)).secrets.set(
        AI_CLAUDE_CODE_TOKEN_SECRET, value, updated_by=actor
    )
    if masked is not None:
        return {"ok": True, "set": True, "masked": masked}, 200
    return {"ok": True, "set": False}, 200


def git_credential_info(state: ServeState, actor: str | None) -> tuple[Any, int]:
    """Whether a Git config-source credential is set, with a masked preview — never plaintext (BE-0224).

    The write-once counterpart to `api_key_info` for the Git credential: same shape, same store, read
    from the actor's org so a hosted deployment scopes it per org. No `reveal`, no `value` field."""
    masked = state.for_org(state.org_of(actor)).secrets.describe(GIT_CONFIG_TOKEN_SECRET)
    payload: dict[str, Any] = {"set": masked is not None}
    if masked is not None:
        payload["masked"] = masked
    return payload, 200


def set_git_credential(state: ServeState, value: str, actor: str | None) -> tuple[Any, int]:
    """Set or replace the Git config-source credential (an empty *value* clears it), through the same
    write-once secret store (BE-0224). The response redacts what was stored — never the plaintext.
    Local serve holds it in ``BAJUTSU_GIT_CONFIG_TOKEN`` for the in-process private-repo fetch (and
    spawned jobs) to read — not ``GITHUB_TOKEN``, so clearing it never pops an operator's own exported
    token; a hosted deployment encrypts it per org. Mirrors `set_api_key`; overwriting rotates it."""
    value = value.strip()
    if value and any(c.isspace() for c in value):
        return {"error": "the credential must not contain whitespace"}, 400
    masked = state.for_org(state.org_of(actor)).secrets.set(
        GIT_CONFIG_TOKEN_SECRET, value, updated_by=actor
    )
    if masked is not None:
        return {"ok": True, "set": True, "masked": masked}, 200
    return {"ok": True, "set": False}, 200


def declared_secret_names(state: ServeState) -> list[str]:
    """The scenario secret env-var names the bound config declares, union across targets (BE-0274).

    A config's ``secrets:`` list (per-target, merged over defaults) names the environment variables
    ``${secrets.X}`` resolves at run time (BE-0032). A scenario can run against any target, so the
    panel offers the union of every target's effective list, order-preserving. Names that fail the
    ``_valid_key_env_name`` guard (a non-identifier, or a system variable like ``PATH``) are dropped
    so the UI never offers — nor the write path accepts — an unsafe env-var write; a name in
    ``_RESERVED_SECRET_NAMES`` is dropped too, so a scenario secret can never alias the AI key, the
    Claude Code OAuth token, or the Git credential. No config bound, an empty ``secrets:``, or a
    config that fails to load yields an empty list (the panel then shows nothing to configure); a
    load failure is logged at debug, matching ``active_key_env``."""
    if state.config is None:
        return []
    try:
        cfg = load_config(state.config.read_text(encoding="utf-8"))
        names: dict[str, None] = {}
        for target in cfg.targets:
            for name in resolve(cfg, target).secrets:
                if _valid_key_env_name(name) and name not in _RESERVED_SECRET_NAMES:
                    names.setdefault(name, None)
        return list(names)
    except Exception:
        logging.getLogger(__name__).debug(
            "cannot resolve declared secrets from config", exc_info=True
        )
        return []


def scenario_secrets_info(state: ServeState, actor: str | None) -> tuple[Any, int]:
    """The scenario secrets the bound config declares, each with whether it is set and a masked
    preview — never the plaintext (BE-0274).

    One entry per declared name (`declared_secret_names`), `masked` read from the actor's org secret
    store so a hosted deployment scopes it per org. Write-once, like `api_key_info`: no `value` field
    ever, for any role. An empty list when no config is bound or it declares no `secrets:`."""
    bundle = state.for_org(state.org_of(actor))
    out = []
    for name in declared_secret_names(state):
        masked = bundle.secrets.describe(name)
        out.append({"name": name, "set": masked is not None, "masked": masked})
    return out, 200


def set_scenario_secret(
    state: ServeState, body: dict[str, Any], actor: str | None
) -> tuple[Any, int]:
    """Set or replace a scenario-declared secret (an empty *value* clears it), through the write-once
    secret store (BE-0274). The response redacts what was stored — never the plaintext.

    Only a name the bound config's ``secrets:`` actually declares is accepted (400 otherwise), which
    keeps this from being an arbitrary-environment-variable-write primitive. `declared_secret_names`
    has already dropped any name that fails the ``_valid_key_env_name`` guard (a non-identifier, or a
    system variable like ``PATH``) or that aliases a reserved operator credential (the AI key, the
    Claude Code OAuth token, the Git credential), so a name that passes the membership check is safe
    to write. Local serve holds the value in the process env under its own declared name for a spawned
    run to inherit
    (`${secrets.X}` resolves there); a hosted deployment encrypts it per org. Unlike the operator
    credentials there is no whitespace guard — a scenario secret (a login password, say) may
    legitimately contain spaces."""
    name = str(body.get("name", "") or "")
    if name not in declared_secret_names(state):
        return {"error": f"{name!r} is not a secret declared by the bound config"}, 400
    value = str(body.get("value", "") or "")
    masked = state.for_org(state.org_of(actor)).secrets.set(name, value, updated_by=actor)
    if masked is not None:
        return {"ok": True, "set": True, "masked": masked}, 200
    return {"ok": True, "set": False}, 200


def set_provider(state: ServeState, body: dict[str, Any], actor: str | None) -> tuple[Any, int]:
    """Select the AI provider for the actor's org's spawned record/crawl jobs: the Anthropic API
    (`api-key`), Amazon Bedrock, the Anthropic CLI (`ant`, a browser-based OAuth/SSO sign-in —
    BE-0163), or the Claude Code CLI (`claude-code`, the local `claude` on a Pro/Max/Console seat —
    BE-0176). Stored per organization (BE-0229) and materialized into a per-job env overlay at
    dispatch, rather than mutating the shared process env — so on a hosted multi-tenant serve one
    org's save never changes another org's AI runs. On a wired deployment the choice is flushed to
    the org's store so it survives a restart (BE-0184 / BE-0229). The selection is remembered per
    provider (BE-0183) so switching the Settings dropdown no longer discards the model/effort set for
    the provider left behind; only the selected provider's slot is written — the others are
    untouched. Validated against the BE-0104 registry's *selectable* names, so every AI path
    (authoring, the alert guard, triage) resolves through the same seam while the `none` kill switch
    stays out of the dropdown: it is a statement a repository commits, and a selection here would
    reach a job only as an env var the config outranks (BE-0394)."""
    prov = normalize_provider(str(body.get("provider", "") or ""))
    if prov == DISABLED_PROVIDER:
        # Registered, so "unknown provider" would send the caller off to fix a name that is in fact
        # correct. Say why it is not on offer instead (BE-0394).
        return {
            "error": (
                "the none provider is not selectable here: it disables every AI path and belongs in "
                "the project's ai.provider config, which outranks this setting"
            )
        }, 400
    if prov not in selectable_providers():
        return {"error": f"unknown provider: {prov or '(empty)'}"}, 400
    # Reasoning effort applies to any provider that supports it (claude-code); a blank value clears
    # it and an unknown level is rejected so a typo is a visible error, not a silent default.
    effort = str(body.get("effort", "") or "").strip().lower()
    if effort and effort not in EFFORT_LEVELS:
        return {"error": f"unknown effort {effort!r}: use one of {', '.join(EFFORT_LEVELS)}"}, 400
    # AI output language (BE-0188): applies to record/crawl's generated prose, never the run/CI
    # verdict. `auto` (and blank) is the no-override default, so it stores no override; an unknown
    # value is rejected so a typo is a visible error, not a silent default. It is a global,
    # non-per-provider setting, so it is stored on the org's selection, not the per-provider slot.
    language = str(body.get("language", "") or "").strip().lower()
    if language and language not in LANGUAGES:
        return {"error": f"unknown language {language!r}: use one of {', '.join(LANGUAGES)}"}, 400
    lang_value = language if language and language != DEFAULT_LANGUAGE else ""
    if prov in ("api-key", "ant", "claude-code"):
        # These providers take a bare Anthropic model id via the general override (blank = default).
        ai_model = str(body.get("aiModel", "") or "").strip()
        if any(c.isspace() for c in ai_model):
            return {"error": "model must not contain whitespace"}, 400
        settings = ProviderSettings(model=ai_model, effort=effort)
        org = state.org_of(actor)
        # Load the org's persisted slots first, so writing this one provider's slot doesn't drop the
        # others (set_org_provider_choice merges into the loaded entry).
        _org_settings(state, org)
        state.providers.set_org_provider_choice(
            org, provider=prov, slot=settings, language=lang_value
        )
        persisted = _persist_provider_settings(state, org, prov)
        return {
            "ok": True,
            "provider": prov,
            "model": ai_model,
            "effort": effort,
            "language": language,
            # False only when a durable save was attempted and failed (BE-0184), so the Settings
            # panel can warn the choice won't survive a restart; True when saved or session-only.
            "persisted": persisted,
        }, 200
    # Bedrock needs a provider-prefixed model id (the bare Anthropic id is invalid there); region is
    # optional and falls back to the AWS_REGION the job inherits.
    model = str(body.get("model", "") or "").strip()
    region = str(body.get("region", "") or "").strip()
    if not model:
        return {"error": "a Bedrock model id is required"}, 400
    if any(c.isspace() for c in model) or any(c.isspace() for c in region):
        return {"error": "region and model must not contain whitespace"}, 400
    settings = ProviderSettings(model=model, effort=effort, region=region)
    org = state.org_of(actor)
    _org_settings(state, org)
    state.providers.set_org_provider_choice(
        org, provider="bedrock", slot=settings, language=lang_value
    )
    persisted = _persist_provider_settings(state, org, "bedrock")
    return {
        "ok": True,
        "provider": "bedrock",
        "region": region,
        "model": model,
        "effort": effort,
        "language": language,
        "persisted": persisted,
    }, 200


def _persist_provider_settings(state: ServeState, org: str, provider: str) -> bool | None:
    """Write *provider* + *org*'s current in-memory slot map to that org's durable store (BE-0229).

    The write is race-safe (`ProviderSettingsManager.persist` re-snapshots under its own lock, BE-0248)
    but the active-`provider` field is only best-effort under concurrency — two simultaneous saves for
    different providers leave whichever finished last, which is fine since BE-0184 only promises
    surviving a restart, not atomicity. The selection already took effect in memory, so a write
    failure is logged loudly rather than failing the request; `except Exception` (not a narrower type)
    is deliberate since this seam backs both the local store (`OSError`) and the hosted
    `DbProviderSettingsStore` (a SQLAlchemy error) without importing SQLAlchemy here (BE-0112). The
    language (BE-0188) is never persisted, only kept in memory.

    Returns:
        ``True`` when durably saved, ``False`` when a wired store's write failed (active for the
        session but won't survive a restart), or ``None`` when no store is wired for the org.
    """
    store = state.for_org(org).provider_settings
    if store is None:
        return None
    try:
        # The manager owns the re-snapshot-under-`_persist_lock` discipline (BE-0248); this caller
        # keeps only the store resolution, failure handling, and persisted/not-persisted signaling.
        state.providers.persist(org, provider, store)
    except Exception:
        logging.getLogger(__name__).warning(
            "the AI provider selection is active for this session but could not be persisted; "
            "it will not survive a restart",
            exc_info=True,
        )
        return False
    return True


def restore_persisted_provider_settings(state: ServeState) -> None:
    """Force-load the `default` org's persisted provider selection on serve boot (BE-0229).

    A boot-time trigger for the lazy per-org load (`_org_settings`), kept as its own seam so a
    malformed store is logged loudly at startup, not on the first request. Local serve has only the
    `default` org, so loading it here restores exactly what the operator last saved. With no store
    wired or nothing saved, this is a silent no-op — resolution falls back to today's env-derived
    defaults, keeping the AI-free zero-config path (BE-0101) unchanged. Per-org (hosted) selections
    load lazily on each org's first request instead of at boot."""
    _org_settings(state, DEFAULT_ORG)


def launch_label(config: Path, provenance: dict[str, str] | None) -> str:
    """The default run-history label for the bound config (BE-0404 unit 2).

    A Git-materialized config (its *provenance* stamp is present) is named ``<repo>/<path>`` — the
    repository plus the config's path within it — so two configs from one repository partition
    apart rather than folding onto the repository name. Any other config is a local file, named for
    its file stem. An operator overrides this per run with ``--label``.
    """
    if provenance is not None and "repo" in provenance:
        path = provenance.get("path") or ""
        return f"{provenance['repo']}/{path}" if path else provenance["repo"]
    return config.stem


def _valid_slot(name: str, settings: ProviderSettings) -> bool:
    """Whether a persisted slot has values that set_provider would have accepted (BE-0184).

    Mirrors the input validation in set_provider so a hand-edited or stale file can't slip an
    invalid effort level or a whitespace-containing model/region through on boot.
    """
    if settings.effort and settings.effort not in EFFORT_LEVELS:
        return False
    if name == "bedrock":
        # A blank Bedrock model is invalid — bedrock always needs a provider-prefixed model id.
        # `not any(c.isspace() for c in "")` is True, so the explicit `bool(settings.model)` is
        # necessary to catch an empty string that the whitespace check alone would pass.
        return (
            bool(settings.model)
            and not any(c.isspace() for c in settings.model)
            and not any(c.isspace() for c in settings.region)
        )
    return not any(c.isspace() for c in settings.model)


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

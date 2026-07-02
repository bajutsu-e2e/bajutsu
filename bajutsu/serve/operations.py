"""Framework-agnostic serve operations (BE-0015).

The orchestration behind each serve endpoint, lifted out of the stdlib HTTP handler so the local
stdlib server and the hosted-backend FastAPI control plane share **one** implementation —
local/server parity reaching the request-handling layer, not just the four swap-in seams. Each
function takes the `ServeState` plus already-parsed inputs and returns ``(payload, status)``; the
HTTP shells own only the transport-specific parts (auth / CSRF / cookies / headers, JSON encoding,
SSE streaming, static asset serving).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
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
from bajutsu.backends import IMPLEMENTED, resolve_actuators
from bajutsu.config import load_config, resolve, targets_for_org
from bajutsu.config_source import materialize, parse_config_spec, source_provenance
from bajutsu.drivers import base as driver_base
from bajutsu.redaction import Redactor
from bajutsu.scenario import load_scenario_file
from bajutsu.scenario.models import STEP_ACTIONS, Step
from bajutsu.serve import jobs, oplog

# Identity / RBAC / audit live in `authz` now; re-exported here so the HTTP shells keep reaching
# them through the `operations` facade (`ops.login`, `ops.forbidden_for_role`, …) unchanged.
# `_target_forbidden` / `_record_audit` are also used internally by the endpoints below; the rest are
# pure re-exports, declared in `__all__` so they read as intentional public API, not dead imports.
from bajutsu.serve.authz import (
    _record_audit,
    _target_forbidden,
    forbidden_for_role,
    login,
    oauth_callback,
    oauth_login,
    required_role,
    role_allows,
    role_for,
)

# The auth surface re-exported through this facade (the endpoint functions defined below are reached
# by attribute access, so they need no entry here). Keeps the re-exports off the unused-import lint.
__all__ = [
    "forbidden_for_role",
    "login",
    "oauth_callback",
    "oauth_login",
    "required_role",
    "role_allows",
    "role_for",
]
from bajutsu.serve.artifacts import Artifact, ArtifactStore
from bajutsu.serve.helpers import (
    _int,
    crawl_command,
    list_fs,
    list_simulators,
    list_targets,
    load_config_file,
    mask_secret,
    record_command,
    run_command,
    target_build_info,
    valid_backend,
    valid_run_id,
    valid_scenario_ref,
    valid_udid,
)
from bajutsu.serve.jobs import Job, ServeState
from bajutsu.serve.uploads import BundleError, Upload, extract_bundle, find_bundle_config

_REPORT_SUFFIX = "/report.html"

_logger = logging.getLogger("bajutsu.serve.operations")


def run_file(store: ArtifactStore, rel: str) -> Artifact | None:
    """Serve a run-relative artifact, rendering `report.html` **on view** (BE-0068).

    For `<run_id>/report.html` the report is rendered fresh from the stored model with the current
    template (`store.render_report`), falling back to the baked file when the model can't be loaded;
    any other artifact (screenshots, videos, manifest.json, …) is served byte-for-byte.
    """
    if rel.endswith(_REPORT_SUFFIX):
        # `render_report` validates + confines the run id itself (returning None for a non-run or a
        # nested path), so containment stays in one place and we fall back to the baked file via get.
        rendered = store.render_report(rel[: -len(_REPORT_SUFFIX)])
        if rendered is not None:
            return rendered
    return store.get(rel)


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


def _boot_targets(udid: str) -> list[str]:
    """The concrete devices to boot before a run/record/crawl. Picked devices are booted (and
    waited on) first; the "booted" alias names whatever is already up, so it's not a boot target."""
    return [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]


def _device_args(body: dict[str, Any]) -> tuple[str, str, tuple[Any, int] | None]:
    """Parse + validate the device selectors common to run/record/crawl: ``(backend, udid, error)``.
    *error* is a ``(payload, status)`` tuple when a value is invalid (a free-text backend/udid must
    not reach the spawned argv — BE-0051), else None so the caller proceeds."""
    backend = str(body.get("backend", "") or "")
    if backend and not valid_backend(backend):
        return backend, "", ({"error": f"unknown backend: {backend}"}, 400)
    udid = str(body.get("udid", "") or "")
    if udid and not valid_udid(udid):
        return backend, udid, ({"error": "invalid udid"}, 400)
    return backend, udid, None


def _bool_flag(body: dict[str, Any], key: str) -> bool | None:
    """A tri-state flag from the request body: True/False when explicitly set, else None (so the
    spawned CLI applies the scenario/CLI default rather than being forced either way)."""
    value = body.get(key)
    return value if isinstance(value, bool) else None


# --- GET (reads) ---


def list_scenarios(
    state: ServeState, target: str | None, *, actor: str | None = None
) -> tuple[Any, int]:
    # Hide a target that belongs to another org (non-leaky: an empty list, not a 403) — BE-0015
    # multi-tenancy. The scenarios come from the actor's org-scoped store.
    org = state.org_of(actor)
    if target is not None and _target_forbidden(state, org, target):
        return [], 200
    scope = state.for_org(org).scenarios.scope(target)
    return (scope.list() if scope else []), 200


def _primary_backend(config: Any, name: str) -> str:
    """The target's first (effective) backend token, so the Web UI can tell web from iOS apps."""
    target = config.targets.get(name)
    if target is None:
        return ""
    backends = target.backend or config.defaults.backend
    return backends[0] if backends else ""


def list_targets_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    # Each target carries its primary backend, so selecting a web target can hide the iOS-only controls
    # (and show the headed toggle) without the user typing the backend by hand.
    if state.config is None:
        return [], 200
    config = load_config_file(state.config)
    if config is None:
        return [], 200
    # Org scoping applies only on a server backend with a system of record; local serve / token mode
    # ignores `orgs:` and lists every target (BE-0015 multi-tenancy).
    if state.repository is None:
        names = list_targets(state.config)
    else:
        names = sorted(targets_for_org(config, state.org_of(actor)))
    return [{"name": n, "backend": _primary_backend(config, n)} for n in names], 200


def config_info(state: ServeState) -> tuple[Any, int]:
    return {
        "config": str(state.config) if state.config else None,
        "hasConfig": state.config is not None,
        "root": str(state.root.resolve()),
        # Whether GitHub OAuth login is available, so the login UI can offer a button (BE-0015 7b-2).
        "oauthEnabled": state.oauth is not None,
    }, 200


def browse_fs(state: ServeState, sub: str | None) -> tuple[Any, int]:
    try:
        return list_fs(state.root, sub), 200
    except (ValueError, OSError) as e:
        return {"error": str(e)}, 400


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


def simulators_payload(state: ServeState) -> tuple[Any, int]:
    return list_simulators(state.simctl), 200


def runs_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    # With a system of record (server backend), the history is the actor's org's recorded runs —
    # durable and org-scoped (BE-0015 7c-4). The stored summary mirrors the artifact entry, so the
    # UI shape is identical. Without one (local / stdlib serve), list straight from the artifact
    # store.
    if state.repository is not None:
        return [r.summary for r in state.repository.list_runs(org_id=state.org_of(actor))], 200
    return state.artifacts.list_runs(), 200


def read_scenario(
    state: ServeState,
    target: str | None,
    path: str | None,
    *,
    actor: str | None = None,
    run_id: str | None = None,
    scenario_name: str | None = None,
) -> tuple[Any, int]:
    # A scenario in another org's target reads as not-found (non-leaky) — BE-0015 multi-tenancy.
    org = state.org_of(actor)
    if target is not None and _target_forbidden(state, org, target):
        return {"error": "not found"}, 404
    scope = state.for_org(org).scenarios.scope(target)
    text = scope.read(path) if scope else None
    if text is None:
        return {"error": "not found"}, 404
    if not run_id:
        return {"yaml": text}, 200
    if not valid_run_id(run_id):
        return {"yaml": text, "steps": []}, 200
    return {"yaml": text, "steps": _step_artifacts(state, text, run_id, scenario_name)}, 200


def _step_artifacts(
    state: ServeState,
    yaml_text: str,
    run_id: str,
    scenario_name: str | None,
) -> list[dict[str, Any]]:
    """Build per-step artifact handles for the editor (BE-0013)."""
    try:
        scenarios = load_scenario_file(yaml_text).scenarios
    except (ValueError, Exception):
        return []

    manifest_path = state.runs_dir / run_id / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    matched = (
        next((s for s in scenarios if s.name == scenario_name), None) if scenario_name else None
    )
    if matched is None and scenarios:
        matched = scenarios[0]
    if matched is None:
        return []

    effective_name = scenario_name or (matched.name if matched else None)
    sid = _find_sid(manifest, effective_name)
    if sid is None:
        return []

    result: list[dict[str, Any]] = []
    for idx, step in enumerate(matched.steps):
        step_id = f"{sid}/{step.name or f'step{idx}'}"
        step_dir = state.runs_dir / run_id / step_id
        elements_file = step_dir / "elements.json"
        screenshot_file = step_dir / "after.png"
        action, fields = _step_action_fields(step)
        result.append(
            {
                "stepId": step_id,
                "action": action,
                "fields": fields,
                "elementsUrl": f"/runs/{run_id}/{step_id}/elements.json"
                if elements_file.is_file()
                else None,
                "screenshotUrl": f"/runs/{run_id}/{step_id}/after.png"
                if screenshot_file.is_file()
                else None,
            }
        )
    return result


def _step_action_fields(step: Step) -> tuple[str, Any]:
    """Extract the action kind and its fields from a parsed Step.

    Fields may be a dict (tap, type, …) or a list (assert).
    """
    dumped = step.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
    for field_name in STEP_ACTIONS:
        alias = Step.model_fields[field_name].alias or field_name
        if alias in dumped:
            return alias, dumped[alias]
    return "unknown", {}


def _valid_step_id(step_id: str) -> bool:
    """Whether *step_id* is a safe relative path (no traversal, no absolute)."""
    if not step_id or step_id.startswith("/"):
        return False
    parts = Path(step_id).parts
    return ".." not in parts


def _find_sid(manifest: dict[str, Any], scenario_name: str | None) -> str | None:
    """Find the evidence-dir slug for *scenario_name* in the manifest."""
    for scn in manifest.get("scenarios", []):
        if scn.get("scenario") == scenario_name:
            return scn.get("sid") or None
    return None


def job_view(state: ServeState, job_id: str) -> tuple[Any, int]:
    job = state.jobs.get(job_id)
    if job is None:
        return {"error": "no such job"}, 404
    view = job.view()
    # Locally the job ran in-process, so its own view (with the log buffer) is authoritative. On the
    # server backend it ran on a worker and the control-plane Job stays "running"; fall back to the
    # bus's terminal status then (BE-0015 W2).
    if view["status"] != "done":
        final = state.logbus.final(job_id)
        if final is not None:
            return json.loads(final), 200
    return view, 200


# --- live-log SSE (shared event stream; each shell does its own framing/transport) ---


def format_sse(event: str, data: str) -> str:
    """One Server-Sent Event frame. *data* is split on line breaks into one ``data:`` line each,
    ended by a single blank line so the browser dispatches it. Splitting matters: a value with an
    embedded newline (a multi-line or crafted log line) would otherwise inject extra SSE fields
    (e.g. a fake ``event:``), and a LogBus line's trailing newline would add a stray blank line."""
    body = "".join(f"data: {line}\n" for line in data.splitlines()) or "data: \n"
    return f"event: {event}\n{body}\n"


def job_log_events(state: ServeState, job_id: str) -> Iterator[tuple[str, str]] | None:
    """The live-log stream for *job_id* as ``(event, data)`` pairs — a ``log`` per line (backlog +
    live from the LogBus), then a terminal ``done`` carrying the job's final view — or None if the
    job is unknown. The buffered bus means a subscriber that attaches after the job finished still
    replays everything. The blocking iteration is the caller's to host (a thread per request)."""
    job = state.jobs.get(job_id)
    if job is None:
        return None
    return _job_event_pairs(state, job, job_id)


def _job_event_pairs(state: ServeState, job: Job, job_id: str) -> Iterator[tuple[str, str]]:
    for line in state.logbus.stream(job_id):
        if line is not None:  # no timeout passed, so no heartbeats — guard only satisfies the type
            yield ("log", line)
    yield ("done", _terminal_payload(state, job, job_id))


def _terminal_payload(state: ServeState, job: Job, job_id: str) -> str:
    """The job's terminal status (JSON): the worker-recorded view on the bus (server backend), else
    the local Job's (BE-0015 W2). The stream has ended, so `close` ran and any final payload is set.
    Lines are omitted — they already arrived as `log` events, so the done payload needn't repeat
    them."""
    final = state.logbus.final(job_id)
    return final if final is not None else json.dumps(job.view(include_lines=False))


def job_sse(state: ServeState, job_id: str, *, keepalive: float) -> Generator[str] | None:
    """The job's log as ready-to-send SSE strings — `log` frames, a terminal `done` frame, and a
    ``:keepalive`` comment whenever the stream is idle for *keepalive* seconds (so a reverse proxy
    won't drop the connection during a quiet stretch) — or None if the job is unknown (BE-0015).
    A generator so the caller can ``close()`` it to stop the underlying stream on a disconnect."""
    job = state.jobs.get(job_id)
    if job is None:
        return None
    return _job_sse_frames(state, job, job_id, keepalive)


def _job_sse_frames(state: ServeState, job: Job, job_id: str, keepalive: float) -> Generator[str]:
    for line in state.logbus.stream(job_id, timeout=keepalive):
        yield ":keepalive\n\n" if line is None else format_sse("log", line)
    yield format_sse("done", _terminal_payload(state, job, job_id))


# --- Doctor / preflight (BE-0024) ---


def doctor_check(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Run preflight environment checks for a target: config validation + tool runnability.

    Returns structured JSON so the web UI can show a health-check panel before a run —
    the same checks the CLI ``bajutsu doctor`` runs, minus the live screen score (which
    needs a device connection the web UI might not have yet). The ``ok`` top-level boolean
    is true only when every individual check passed.
    """
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    target = str(body["target"])

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400

    eff = resolve(config, target)

    # Resolve the *intended* actuator without requiring it to be installed — select_actuator
    # raises when the tool is absent, but doctor's purpose is to *report* what's missing, so we
    # resolve the first implemented actuator from the backends list and let the runnability
    # checks surface the absent tool.  Filtering against IMPLEMENTED (not KNOWN_ACTUATORS)
    # avoids picking a planned-but-unimplemented backend (e.g. adb) whose preflight would
    # fall through to generic checks.
    actuators = resolve_actuators(eff.backend)
    implemented = [a for a in actuators if a in IMPLEMENTED]
    if not implemented:
        return {"error": f"no implemented backend among {eff.backend}"}, 400
    actuator = implemented[0]

    from bajutsu import preflight

    cfg_checks = preflight.config_checks(
        actuator, target=target, bundle_id=eff.bundle_id, base_url=eff.base_url
    )
    env_checks = preflight.runnability(actuator, web_engine=eff.browser)
    all_checks = cfg_checks + env_checks

    serialized = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in all_checks]
    return {
        "ok": preflight.passed(all_checks),
        "checks": serialized,
        "target": target,
        "backend": actuator,
    }, 200


# --- POST (actions) ---


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


def _resolve_org_or_forbid(
    state: ServeState, target: str, actor: str | None
) -> tuple[str, tuple[Any, int] | None]:
    """The org resolution + cross-org guard shared by every start_* endpoint: resolve the actor's
    org and deny a target that belongs to another org (BE-0015; single-tenant never forbids).
    Returns ``(org, None)`` when allowed, or ``(org, (error, 403))`` for the caller to return."""
    org = state.org_of(actor)
    if _target_forbidden(state, org, target):
        return org, ({"error": "forbidden"}, 403)
    return org, None


def _register_and_dispatch(
    state: ServeState, job: Job
) -> tuple[Job | None, tuple[Any, int] | None]:
    """Register *job* under the concurrency cap and dispatch it, the tail shared by every start_*
    endpoint. Returns ``(job, None)`` once dispatched, or ``(None, (error, 429))`` when the cap is
    hit. The atomic count+create in `try_register` is what keeps concurrent dispatches under the cap."""
    registered = state.try_register(job)
    if registered is None:
        oplog.log_event(
            _logger,
            "quota.rejected",
            "concurrency cap hit; job rejected",
            org=job.org,
            actor=job.actor,
        )
        return None, ({"error": "too many concurrent jobs; try again shortly"}, 429)
    state.executor.dispatch(state, registered)
    oplog.log_event(
        _logger,
        "run.dispatched",
        "job dispatched",
        job_id=registered.id,
        org=registered.org,
        actor=registered.actor,
    )
    return registered, None


def start_run(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("scenario") or not body.get("target"):
        return {"error": "scenario and target are required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    # Confine the scenario to the target's own scenarios dir: a serve client must not be able to run an
    # arbitrary file path on the host (BE-0051 / BE-0015 / BE-0016 prerequisite). The scenario store
    # is scoped to the actor's org so the run reads that org's scenarios.
    scope = state.for_org(org).scenarios.scope(target)
    if scope is None:
        return {"error": f"target '{target}' has no scenarios dir"}, 400
    # The store resolves the client value to a trusted runnable — never the client string — so no
    # client-controlled value reaches a filesystem path (BE-0051 arbitrary-path guard). On the
    # server backend it also carries the scenario text as `materials` for a remote worker.
    runnable = scope.runnable(str(body["scenario"]))
    if runnable is None:
        return {
            "error": "scenario must be an existing .yaml inside the target's scenarios dir"
        }, 400
    backend, udid, err = _device_args(body)
    if err:
        return err
    # When the scenario ships as materials (server backend), the worker has no project on disk, so
    # the config travels too and the run uses workspace-relative paths; locally nothing materializes
    # and the run uses the real config / baselines paths.
    materials = dict(runnable.materials)
    on_worker = bool(materials)
    config_arg = "bajutsu.config.yaml" if on_worker else str(cfg)
    if on_worker:
        materials[config_arg] = cfg.read_text(encoding="utf-8")
    # A config bound from a different cwd — a Git checkout (BE-0063) or an uploaded bundle (BE-0073) —
    # runs from that tree, so a run would otherwise write its output under the transient checkout/
    # bundle. Point --runs-dir at serve's own store (an absolute path under base_cwd) so the run lands
    # in history and survives the bundle being replaced. Local path only — a server-backend run
    # materializes into a worker workspace instead (on_worker).
    runs_dir = (
        str((state.base_cwd / state.runs_dir).resolve())
        if not on_worker and state.cwd != state.base_cwd
        else ""
    )
    # On the worker, baselines are downloaded into a workspace-relative dir before the run (the
    # control plane's baselines live in object storage); locally the real dir is used directly.
    cmd = run_command(
        runnable.arg,
        target,
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        erase=_bool_flag(body, "erase"),
        dismiss_alerts=_bool_flag(body, "dismissAlerts"),
        config=config_arg,
        # An uploaded bundle is self-contained: omit --baselines so its config's `baselines` drives
        # (resolved against the bundle cwd), like the rest of its relative paths (BE-0073).
        baselines=""
        if state.upload is not None
        else ("baselines" if on_worker else str(state.baselines_dir)),
        headed=_bool_flag(body, "headed"),
        runs_dir=runs_dir,
        # Govern the uploaded bundle's launchServer command (BE-0090); a local/Git config is
        # operator-trusted and ungoverned, so it gets no flag.
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, target)
    if state.upload is not None:
        # An uploaded bundle ships a prebuilt binary; never run its (untrusted) `build` command on the
        # host — DESIGN §1 "Bajutsu does not build the app" (BE-0073). appPath was confined to the
        # bundle at bind. The bundle's provenance is stamped into the run's manifest after it finishes.
        build = None
    job, capped = _register_and_dispatch(
        state,
        Job(
            cmd=cmd,
            udids=_boot_targets(udid),
            app_path=app_path,
            build=build,
            materials=materials,
            materialize_baselines=on_worker,
            provenance=state.upload.provenance if state.upload is not None else None,
            actor=actor,
            org=org,
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(
        state, actor, org, "run", f"{target}/{body['scenario']}", {"backend": backend or None}
    )
    return {"jobId": job.id}, 200


# --- Upload a bundle as the active config (BE-0073): config + scenarios + app binary as one zip ---


def _safe_filename(name: str) -> str:
    """A display-safe basename for the uploaded zip (provenance only): strip any directory and
    non-printable characters, bound the length, and fall back to a default when nothing remains."""
    base = "".join(c for c in Path(name or "").name if c.isprintable()).strip()
    return (base or "bundle.zip")[:200]


def bind_upload_config(
    state: ServeState, zip_path: Path, filename: str, *, sha256: str, actor: str | None = None
) -> tuple[Any, int]:
    """Bind an uploaded zip bundle as the active config (BE-0073) — a third source in the "Open
    config" UI, alongside the file browser and the Git picker.

    The zip is a self-contained checkout — a ``bajutsu.config.yaml``, its scenario tree, and the
    built ``appPath`` binary it names — delivered over the wire. *sha256* is the digest the handler
    computed while streaming the upload to *zip_path* (so the file is read once, not again to hash).
    We extract it into a serve-owned sandbox, then bind it exactly like the Git source binds a
    checkout (`bind_git_config`): `state.config` points at the bundle's config and `state.cwd` at the
    bundle root, so the config's relative `appPath`/`scenarios`/`baselines` resolve against the
    extracted tree and the Replay / Record / Crawl tabs all run from it. Every target's path fields
    are confined to the bundle at bind (`Effective.rebased`), so an uploaded config can't point
    serve's scenario/build logic at host paths outside the tree (BE-0051). Only one bundle is bound at
    a time — binding any other config removes this sandbox (`state.bind_upload`). Returns
    `{config, targets, source}` like the other sources; on any validation failure the freshly-extracted
    dir is removed and a 4xx is returned."""
    size = zip_path.stat().st_size
    state.uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(dir=state.uploads_dir))
    try:
        extract_bundle(zip_path, dest)
    except BundleError as e:
        shutil.rmtree(dest, ignore_errors=True)
        return {"error": f"invalid bundle: {e}"}, 400
    config_path = find_bundle_config(dest)
    if config_path is None:
        shutil.rmtree(dest, ignore_errors=True)
        return {"error": "bundle has no bajutsu.config.yaml"}, 400
    try:
        cfg = load_config(config_path.read_text(encoding="utf-8"))
        # Confine every target's path fields to the bundle, the same guard the Git source applies to a
        # fetched checkout (BE-0051): a config pointing appPath/scenarios/baselines at an absolute or
        # `..` path outside the tree is rejected here, so serve's resolution only sees in-bundle paths.
        for name in cfg.targets:
            resolve(cfg, name).rebased(config_path.parent)
    except (OSError, ValueError, yaml.YAMLError) as e:
        shutil.rmtree(dest, ignore_errors=True)
        return {"error": f"invalid bundle: {e}"}, 400
    org = state.org_of(actor)
    upload = Upload(
        dir=dest,
        config=config_path,
        filename=_safe_filename(filename),
        sha256=sha256,
        size=size,
        org=org,
        actor=actor,
    )
    state.bind_upload(upload)
    _record_audit(state, actor, org, "upload", upload.filename, {"sha256": sha256})
    return {
        "ok": True,
        "config": str(config_path),
        "targets": list_targets(config_path),
        "source": {"kind": "upload", "filename": upload.filename, "sha256": sha256, "size": size},
    }, 200


def start_record(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Author a scenario from a natural-language goal (the Record tab).  The authored file lands in
    the selected target's configured scenarios dir."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("goal") or not body.get("target"):
        return {"error": "goal and target are required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    scope = state.for_org(org).scenarios.scope(target)
    if scope is None:
        return {"error": f"target '{body['target']}' has no scenarios dir"}, 400
    authored = scope.authored(str(body.get("name") or "generated"))
    # Validate the device args the same way start_run does (BE-0051): no free-text backend or udid
    # reaches the spawned `bajutsu record` argv. The output path is confined by `authored` above.
    backend, udid, err = _device_args(body)
    if err:
        return err
    # On the server backend (authored.save set) the worker has no project on disk: ship the config
    # and use workspace-relative --out / --config; the worker persists the authored file afterward.
    on_worker = authored.save is not None
    materials: dict[str, str] = {}
    config_arg = str(cfg)
    if on_worker:
        config_arg = "bajutsu.config.yaml"
        materials[config_arg] = cfg.read_text(encoding="utf-8")
    cmd = record_command(
        authored.out,
        body["target"],
        str(body["goal"]),
        agent=body.get("agent", ""),
        backend=backend,
        udid=udid,
        erase=_bool_flag(body, "erase"),
        dismiss_alerts=_bool_flag(body, "dismissAlerts"),
        headed=_bool_flag(body, "headed"),
        config=config_arg,
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, body["target"])
    job, capped = _register_and_dispatch(
        state,
        Job(
            cmd=cmd,
            udids=_boot_targets(udid),
            app_path=app_path,
            build=build,
            out_path=authored.out,
            materials=materials,
            record_save=authored.save,
            actor=actor,
            org=org,
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(state, actor, org, "record", str(body["target"]), {"goal": str(body["goal"])})
    # Report the saved ref on the server (what the UI loads), else the on-disk path.
    return {"jobId": job.id, "path": authored.save[1] if authored.save else authored.out}, 200


def start_crawl(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Explore a target breadth-first and build a screen map (the Crawl tab).  The screen map is
    streamed into ``runs/<runId>/screenmap.json``; the returned ``runId`` lets the UI poll it."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    # Resume continues an existing run (a pruned branch tapped in the UI); otherwise a new run.
    resume_src = str(body.get("resumeSrc", "") or "")
    resume_key = str(body.get("resumeKey", "") or "")
    resuming = bool(resume_src and resume_key and body.get("runId"))
    run_id = str(body["runId"]) if resuming else datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    # A resumed crawl takes runId from the client; reject anything but a safe path segment so
    # `runs_dir / run_id` (the crawl's --out) can't escape runs_dir (BE-0051).
    if resuming and not valid_run_id(run_id):
        return {"error": "invalid runId"}, 400
    backend, udid, err = _device_args(body)
    if err:
        return err
    cmd = crawl_command(
        target,
        out=str(state.runs_dir / run_id),
        agent=body.get("agent", ""),
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        max_screens=_int(body.get("maxScreens"), 50),
        max_steps=_int(body.get("maxSteps"), 200),
        erase=_bool_flag(body, "erase"),
        dismiss_alerts=_bool_flag(body, "dismissAlerts"),
        headed=_bool_flag(body, "headed"),
        config=str(cfg),
        resume_src=resume_src if resuming else "",
        resume_key=resume_key if resuming else "",
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, target)
    # Cap concurrency like run/record: crawl is long and device-heavy (BE-0051 slice 5).
    job, capped = _register_and_dispatch(
        state,
        Job(
            cmd=cmd,
            udids=_boot_targets(udid),
            app_path=app_path,
            build=build,
            actor=actor,
            org=org,
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(state, actor, org, "crawl", target, {"runId": run_id})
    return {"jobId": job.id, "runId": run_id}, 200


def save_scenario(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Save an edited scenario back to its ``*.yaml`` (bounded to the target's scenarios dir)."""
    target = str(body.get("target") or "") or None
    org = state.org_of(actor)
    # Deny saving into another org's target (BE-0015 multi-tenancy); single-tenant never forbids.
    if target is not None and _target_forbidden(state, org, target):
        return {"error": "forbidden"}, 403
    # Resolve the scope and screen the ref before parsing: a non-saveable path is reported ahead of
    # a YAML error (the local store passes an absolute path inside its dir).
    scope = state.for_org(org).scenarios.scope(target)
    ref = body.get("path")
    ref = ref if isinstance(ref, str) else None
    if scope is None or not valid_scenario_ref(ref, allow_absolute=True):
        return {"error": "path must be a *.yaml under the scenarios dir"}, 400
    text = str(body.get("yaml", ""))
    try:
        load_scenario_file(text)
    except (ValueError, OSError, yaml.YAMLError) as e:
        return {"error": f"invalid scenario: {e}"}, 400
    saved = scope.save(ref, text)
    if saved is None:
        return {"error": "path must be a *.yaml under the scenarios dir"}, 400
    return {"ok": True, "path": saved}, 200


def approve_baseline(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Promote a run's captured screenshot to a `visual` baseline.

    Reads ``runs/<runId>/<sid>/visual-actual.png`` from the artifact store and writes it as baseline
    *baseline* via the baseline store — both seams confine the name to their root (filesystem dir or
    object-storage prefix), so a crafted runId / sid / baseline can't read or write outside it. Both
    are scoped to the actor's org, so a run in another org reads as not-found (BE-0015)."""
    run_id = str(body.get("runId") or "")
    sid = str(body.get("sid") or "")
    baseline = str(body.get("baseline") or "")
    if not run_id or not sid or not baseline:
        return {"error": "runId, sid and baseline are required"}, 400
    bundle = state.for_org(state.org_of(actor))
    data = bundle.artifacts.open_bytes(f"{run_id}/{sid}/visual-actual.png")
    if data is None:
        return {"error": "no captured screenshot for this run"}, 404
    if bundle.baselines.write(baseline, data) is None:
        return {"error": "invalid baseline name"}, 400
    return {"ok": True, "baseline": baseline}, 200


def cancel_job(state: ServeState, job_id: str) -> tuple[Any, int]:
    job = state.jobs.get(job_id)
    if job is None:
        return {"error": "no such job"}, 404
    return {"cancelled": jobs.cancel_job(job)}, 200


# --- Capture (BE-0012) ---


def _default_driver_factory(target: str, backend: str, udid: str) -> driver_base.Driver:
    from bajutsu import backends

    actuator = backends.select_actuator([backend] if backend else ["fake"])
    return backends.make_driver(actuator, udid)


def start_capture(
    state: ServeState,
    body: dict[str, Any],
    *,
    actor: str | None = None,
    driver_factory: Any | None = None,
    redactor: Redactor | None = None,
) -> tuple[Any, int]:
    """Open a capture session: boot a live driver, take the initial screenshot + query."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    if state.capture is not None:
        return {"error": "capture session already active"}, 409

    target = str(body["target"])
    _org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400

    backend, udid, err = _device_args(body)
    if err:
        return err
    if not backend:
        backends_list = target_cfg.backend or config.defaults.backend
        backend = backends_list[0] if backends_list else "fake"
    if not udid:
        udid = "booted"

    factory = driver_factory or _default_driver_factory
    driver = factory(target, backend, udid)
    elements = driver.query()

    from bajutsu.capture import screen_size_from_elements

    screen_size = screen_size_from_elements(elements)
    namespaces: list[str] = list(target_cfg.id_namespaces)

    shot_dir = state.runs_dir / "_capture"
    shot_dir.mkdir(parents=True, exist_ok=True)
    shot_path = shot_dir / "screen.png"
    driver.screenshot(str(shot_path))

    state.capture = jobs.CaptureSession(
        driver=driver,
        target=target,
        elements=elements,
        screen_size=screen_size,
        namespaces=namespaces,
        redactor=redactor,
        actor=actor,
        screenshot_path=shot_path,
    )
    return {"ok": True, "screenSize": list(screen_size)}, 200


def mark_capture(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Resolve a point, proxy-actuate, and append the step."""
    session = state.capture
    if session is None:
        return {"error": "no active capture session"}, 400
    if session.actor is not None and actor != session.actor:
        return {"error": "capture session belongs to another user"}, 403

    kind = str(body.get("kind", "tap"))
    point = body.get("point", [0.5, 0.5])
    if not isinstance(point, list) or len(point) != 2:
        return {"error": "point must be [x, y] normalized"}, 400
    try:
        nx, ny = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return {"error": "point values must be numeric"}, 400

    from bajutsu.capture import resolve_capture, step_for_tap, step_for_type

    sw, sh = session.screen_size
    px, py = nx * sw, ny * sh
    result = resolve_capture(session.elements, (px, py), session.namespaces)

    if result.refused:
        return {"refused": result.refused}, 200
    if result.ambiguity:
        return {
            "ambiguity": [
                {"identifier": e["identifier"], "label": e["label"]} for e in result.ambiguity
            ],
            "selector": result.selector.model_dump(exclude_none=True, by_alias=True),
            "rung": result.rung,
        }, 200

    sel = result.selector
    raw = sel.as_selector()

    if kind == "tap":
        session.driver.tap(raw)
        step = step_for_tap(sel)
    elif kind == "type":
        text = str(body.get("text", ""))
        session.driver.tap(raw)
        session.driver.type_text(text)
        step = step_for_type(sel, text, session.redactor)
    else:
        return {"error": f"unsupported capture kind: {kind}"}, 400

    session.steps.append(step)
    session.elements = session.driver.query()
    session.driver.screenshot(str(session.screenshot_path))

    return {
        "selector": sel.model_dump(exclude_none=True, by_alias=True),
        "rung": result.rung,
    }, 200


def finish_capture(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Save the captured scenario and close the session."""
    session = state.capture
    if session is None:
        return {"error": "no active capture session"}, 400

    org, forbidden = _resolve_org_or_forbid(state, session.target, actor)
    if forbidden:
        state.capture = None
        return forbidden

    from bajutsu.scenario.models import Scenario
    from bajutsu.scenario.serialize import dump_scenario_file

    scenario = Scenario(name="captured", steps=list(session.steps))
    yaml_text = dump_scenario_file([scenario])

    scope = state.for_org(org).scenarios.scope(session.target)
    saved: str | None = None
    if scope is not None:
        authored = scope.authored("captured")
        ref = authored.save[1] if authored.save else authored.out
        saved = scope.save(ref, yaml_text)

    state.capture = None
    return {"ok": True, "path": saved, "yaml": yaml_text}, 200


# ---------------------------------------------------------------------------
# Scenario editor — offline resolve against stored artifacts (BE-0013)
# ---------------------------------------------------------------------------


def resolve_scenario_pick(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Resolve a point against a step's stored elements.json — no live driver."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400

    target = str(body.get("target", ""))
    run_id = str(body.get("runId", ""))
    step_id = str(body.get("stepId", ""))
    raw_point = body.get("point")

    if not target:
        return {"error": "target is required"}, 400
    if not run_id or not valid_run_id(run_id):
        return {"error": "invalid or missing runId"}, 400
    if not step_id or not _valid_step_id(step_id):
        return {"error": "invalid or missing stepId"}, 400
    if not isinstance(raw_point, list) or len(raw_point) != 2:
        return {"error": "point must be [x, y] normalized"}, 400
    try:
        nx, ny = float(raw_point[0]), float(raw_point[1])
    except (TypeError, ValueError):
        return {"error": "point must be [x, y] normalized"}, 400

    _org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400
    namespaces: list[str] = list(target_cfg.id_namespaces)

    elements_path = state.runs_dir / run_id / step_id / "elements.json"
    if not elements_path.is_file():
        return {"error": "elements.json not found for this step"}, 404

    try:
        raw = json.loads(elements_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return {"error": "elements.json is not a valid element list"}, 400
        elements: list[driver_base.Element] = [
            {
                "identifier": el.get("identifier"),
                "label": el.get("label"),
                "traits": list(el.get("traits", [])),
                "value": el.get("value"),
                "frame": tuple(el.get("frame", (0, 0, 0, 0))),
            }
            for el in raw
        ]
    except (json.JSONDecodeError, OSError, AttributeError, TypeError):
        return {"error": "elements.json is corrupt or unreadable"}, 400

    from bajutsu.capture import resolve_capture, screen_size_from_elements

    sw, sh = screen_size_from_elements(elements)
    px, py = nx * sw, ny * sh
    result = resolve_capture(elements, (px, py), namespaces)

    if result.refused:
        return {"refused": result.refused}, 200
    if result.ambiguity:
        return {
            "ambiguous": True,
            "selector": result.selector.model_dump(exclude_none=True),
            "rung": result.rung,
            "candidates": len(result.ambiguity),
        }, 200
    return {
        "selector": result.selector.model_dump(exclude_none=True),
        "rung": result.rung,
    }, 200


# ---------------------------------------------------------------------------
# Enrichment — propose assertions for an existing scenario (BE-0014)
# ---------------------------------------------------------------------------


def start_enrich(
    state: ServeState,
    body: dict[str, Any],
    *,
    actor: str | None = None,
    driver_factory: Any | None = None,
    agent_factory: Any | None = None,
) -> tuple[Any, int]:
    """Replay a scenario's steps and propose assertions via an enrichment agent."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    if not body.get("scenario"):
        return {"error": "scenario is required"}, 400

    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400

    scope = state.for_org(org).scenarios.scope(target)
    scenario_text = scope.read(str(body["scenario"])) if scope else None
    if scenario_text is None:
        return {"error": "scenario not found"}, 404

    scenarios = load_scenario_file(scenario_text).scenarios
    if not scenarios:
        return {"error": "no scenarios in file"}, 400

    name = str(body["name"]) if body.get("name") else None
    matched = next((s for s in scenarios if s.name == name), None) if name else scenarios[0]
    if matched is None:
        return {"error": f"scenario '{name}' not found in file"}, 404

    if agent_factory is None:
        from bajutsu.agents import make_enrichment_agent
        from bajutsu.anthropic_client import credential_gap

        eff = resolve(config, target)
        gap = credential_gap(eff.ai)
        if gap:
            return {"error": f"enrichment requires an AI credential ({gap})"}, 400
        agent = make_enrichment_agent(ai=eff.ai)
    else:
        agent = agent_factory()

    backend, udid, err = _device_args(body)
    if err:
        return err
    if not backend:
        backends_list = target_cfg.backend or config.defaults.backend
        backend = backends_list[0] if backends_list else "fake"
    if not udid:
        udid = "booted"

    factory = driver_factory or _default_driver_factory
    driver = factory(target, backend, udid)

    from bajutsu.enrich import enrich

    try:
        proposal = enrich(driver, matched, agent, with_screenshot=False)
    finally:
        close = getattr(driver, "close", None)
        if callable(close):
            close()

    return {
        "ok": True,
        "expect": [a.model_dump(exclude_none=True, by_alias=True) for a in proposal.expect],
        "settle": (
            proposal.settle.model_dump(exclude_none=True, by_alias=True)
            if proposal.settle
            else None
        ),
        "note": proposal.note,
    }, 200


# ---------------------------------------------------------------------------
# Worker HTTP API (BE-0106) — lease jobs and return results over HTTP
# ---------------------------------------------------------------------------


def worker_lease(state: ServeState, worker_id: str) -> tuple[dict[str, Any], int]:
    """Lease the oldest queued job for *worker_id*, or return 204 when the queue is empty."""
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    if not worker_id:
        return {"error": "worker_id is required"}, 400
    leased = state.repository.lease_job(worker_id)
    if leased is None:
        return {}, 204
    return {"job_id": leased.id, "org_id": leased.org_id, "spec": leased.spec}, 200


def worker_result(state: ServeState, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Record a finished job's result (called by the worker after a run completes)."""
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    job_id = body.get("job_id", "")
    result = body.get("result")
    if not job_id:
        return {"error": "job_id is required"}, 400
    if not isinstance(result, dict):
        return {"error": "result must be a JSON object"}, 400
    info = state.repository.get_job(job_id)
    if info is None:
        return {"error": f"job {job_id} not found"}, 404
    if result.get("ok") is False or "error" in result:
        state.repository.fail_job(job_id, error=result.get("error", "unknown"))
    else:
        state.repository.complete_job(job_id, result=result)
    state.logbus.close(job_id, json.dumps(result))
    return {"ok": True}, 200

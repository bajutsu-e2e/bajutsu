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
import os
import secrets
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bajutsu.anthropic_client import (
    BEDROCK_MODEL_ENV,
    PROVIDER_ENV,
    PROVIDERS,
    provider,
)
from bajutsu.config import apps_for_org, load_config, org_for_app, org_for_user
from bajutsu.scenario import load_scenario_file
from bajutsu.serve import jobs
from bajutsu.serve.helpers import (
    _int,
    app_build_info,
    crawl_command,
    list_apps,
    list_fs,
    list_simulators,
    load_config_file,
    mask_secret,
    record_command,
    run_command,
    valid_backend,
    valid_run_id,
    valid_scenario_ref,
    valid_udid,
)
from bajutsu.serve.jobs import _DEFAULT_ORG, Job, ServeState

# The one secret the WebUI lets you set; the AI paths (record, --dismiss-alerts) read it.
_API_KEY_VAR = "ANTHROPIC_API_KEY"


def _boot_targets(udid: str) -> list[str]:
    """The concrete devices to boot before a run/record/crawl. Picked devices are booted (and
    waited on) first; the "booted" alias names whatever is already up, so it's not a boot target."""
    return [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]


# --- GET (reads) ---


def list_scenarios(
    state: ServeState, app: str | None, *, actor: str | None = None
) -> tuple[Any, int]:
    # Hide an app that belongs to another org (non-leaky: an empty list, not a 403) — BE-0015
    # multi-tenancy. The scenarios come from the actor's org-scoped store.
    if app is not None and _org_app_forbidden(state, actor, app):
        return [], 200
    scope = state.for_org(_resolve_org(state, actor)).scenarios.scope(app)
    return (scope.list() if scope else []), 200


def list_apps_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    # Only the apps the actor's org owns (every app for the default org / single-tenant) — BE-0015.
    if state.config is None:
        return [], 200
    config = load_config_file(state.config)
    if config is None:
        return [], 200
    return sorted(apps_for_org(config, _resolve_org(state, actor))), 200


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
    key = os.environ.get(_API_KEY_VAR) or None
    payload: dict[str, Any] = {"set": key is not None}
    if key is not None:
        payload["masked"] = mask_secret(key)
        if reveal:
            payload["value"] = key
    return payload, 200


def provider_info(state: ServeState) -> tuple[Any, int]:
    """The AI provider spawned jobs will use, with the Bedrock region/model.  Read from the serve
    process's environment, so it reflects what a record/crawl job inherits."""
    return {
        "provider": provider(),
        "region": os.environ.get("AWS_REGION", ""),
        "model": os.environ.get(BEDROCK_MODEL_ENV, ""),
    }, 200


def simulators_payload(state: ServeState) -> tuple[Any, int]:
    return list_simulators(state.simctl), 200


def runs_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    # With a system of record (server backend), the history is the actor's org's recorded runs —
    # durable and org-scoped (BE-0015 7c-4). The stored summary mirrors the artifact entry, so the
    # UI shape is identical. Without one (local / stdlib serve), list straight from the artifact
    # store.
    if state.repository is not None:
        org = _resolve_org(state, actor)
        return [r.summary for r in state.repository.list_runs(org_id=org)], 200
    return state.artifacts.list_runs(), 200


def read_scenario(
    state: ServeState, app: str | None, path: str | None, *, actor: str | None = None
) -> tuple[Any, int]:
    # A scenario in another org's app reads as not-found (non-leaky) — BE-0015 multi-tenancy.
    if app is not None and _org_app_forbidden(state, actor, app):
        return {"error": "not found"}, 404
    scope = state.for_org(_resolve_org(state, actor)).scenarios.scope(app)
    text = scope.read(path) if scope else None
    if text is None:
        return {"error": "not found"}, 404
    return {"yaml": text}, 200


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


# --- POST (actions) ---


def login(state: ServeState, token: str) -> tuple[Any, int, str | None]:
    """Validate the shared token and, on success, mint a session id for the shell to set as a
    cookie. Returns ``(payload, status, session_id | None)``."""
    if not state.check_token(token):
        return {"error": "invalid token"}, 401, None
    return {"ok": True}, 200, state.issue_session()


def oauth_login(state: ServeState) -> tuple[Any, int, str | None]:
    """Begin GitHub OAuth (BE-0015 7b-2). Returns the authorize URL to redirect to plus a fresh CSRF
    *state* value the transport sets as a short-lived cookie and compares on callback. 404 when OAuth
    is not configured. Returns ``(payload, status, state | None)``."""
    if state.oauth is None:
        return {"error": "oauth not configured"}, 404, None
    csrf = secrets.token_urlsafe(24)
    return {"redirect": state.oauth.authorize_url(csrf)}, 200, csrf


def oauth_callback(
    state: ServeState, code: str, state_param: str, state_cookie: str
) -> tuple[Any, int, str | None]:
    """Complete GitHub OAuth (BE-0015 7b-2): verify the CSRF state (the query value must match the
    cookie), exchange the code for a GitHub login, check it against the allowlist, and on success mint
    a session bound to that login. Returns ``(payload, status, session_id | None)``."""
    if state.oauth is None:
        return {"error": "oauth not configured"}, 404, None
    if not (state_param and state_cookie and secrets.compare_digest(state_param, state_cookie)):
        return {"error": "invalid oauth state"}, 403, None
    try:
        login = state.oauth.fetch_login(code)
    except Exception:
        # The exchange talks to GitHub (network / token parsing); a failure is an upstream error,
        # not a 500 — surface it as a clean 502 rather than a traceback.
        return {"error": "oauth exchange failed"}, 502, None
    if not login:
        return {"error": "oauth exchange failed"}, 403, None
    if login not in state.oauth_allowed_users:
        return {"error": "user not allowed"}, 403, None
    if state.repository is not None:
        # Persist the identity into the system of record, so audit entries and RBAC can reference
        # the user. The org comes from the config-declared org model (members list), defaulting to
        # the single `default` org. email is unknown from a read:user scope, so we store GitHub's
        # canonical no-reply form (valid + unique per login).
        org = _org_for_login(state, login)
        state.repository.ensure_org(org, slug=org, name=org)
        state.repository.upsert_user(
            login,
            org_id=org,
            github_login=login,
            email=f"{login}@users.noreply.github.com",
            # Recompute the role from the env policy on every login, so changing the policy takes
            # effect on next login without a data migration (BE-0015 7c-2).
            role=role_for(login, admins=state.oauth_admins, viewers=state.oauth_viewers),
        )
    return {"ok": True, "user": login}, 200, state.issue_session(identity=login)


def _org_for_login(state: ServeState, login: str) -> str:
    """The org to assign *login* at OAuth login, from the config-declared org model (BE-0015
    multi-tenancy). The single `default` org when no `orgs:` block lists them."""
    config = load_config_file(state.config)
    return org_for_user(config, login) if config is not None else _DEFAULT_ORG


def _resolve_org(state: ServeState, actor: str | None) -> str:
    """The org of the current *actor* (delegates to `ServeState.org_of`; kept as a module helper so
    the operations read naturally)."""
    return state.org_of(actor)


def _org_app_forbidden(state: ServeState, actor: str | None, app: str) -> bool:
    """True when *actor* may not touch *app* because the app belongs to a different org (BE-0015
    multi-tenancy). Single-tenant (no config, or no `orgs:` block) never forbids: both the app and
    the actor resolve to the default org."""
    config = load_config_file(state.config)
    if config is None:
        return False
    return org_for_app(config, app) != _resolve_org(state, actor)


def _record_audit(
    state: ServeState, actor: str | None, action: str, target: str, detail: dict[str, Any]
) -> None:
    """Append an audit entry (who did what, when) when a database is wired and the actor is known.
    A no-op otherwise — local, no database, or a shared-token request with no identity (BE-0015 7c-1)."""
    if state.repository is None or not actor:
        return
    state.repository.record_audit(
        org_id=_resolve_org(state, actor),
        actor_id=actor,
        action=action,
        target=target,
        detail=detail,
    )


# --- RBAC (BE-0015 7c-2): role-based access control over the mutating endpoints ---

_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}
_ADMIN_PATHS = frozenset({"/api/config", "/api/apikey", "/api/provider"})  # server-wide settings
_EDITOR_PATHS = frozenset(
    {"/api/run", "/api/record", "/api/crawl", "/api/scenario", "/api/approve"}
)


def role_for(login: str, *, admins: frozenset[str], viewers: frozenset[str]) -> str:
    """The role for *login* under the env policy: admin if listed, viewer if listed, else editor
    (the default — an allowlisted user can run). Recomputed on every login (BE-0015 7c-2)."""
    if login in admins:
        return "admin"
    if login in viewers:
        return "viewer"
    return "editor"


def required_role(method: str, path: str) -> str | None:
    """The minimum role a request needs, or None for reads (GET) and the open auth endpoints.
    Cancelling a job is an editor action (it stops a run)."""
    if method != "POST":
        return None
    if path in _ADMIN_PATHS:
        return "admin"
    if path in _EDITOR_PATHS or (path.startswith("/api/jobs/") and path.endswith("/cancel")):
        return "editor"
    return None  # /api/login, /api/oauth/* — authenticated/guarded elsewhere, no role gate


def role_allows(role: str, required: str) -> bool:
    """Whether *role* meets the *required* minimum (viewer < editor < admin)."""
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK.get(required, 0)


def forbidden_for_role(state: ServeState, login: str, method: str, path: str) -> bool:
    """Whether *login* lacks the role for this request — the transport gate calls it for an
    OAuth-authenticated session when a database is wired. A user with no row defaults to viewer."""
    required = required_role(method, path)
    if required is None or state.repository is None:
        return False  # reads, open endpoints, or no database wired (DB-less = full access)
    role = state.repository.user_role(login) or "viewer"  # an unknown user defaults to viewer
    return not role_allows(role, required)


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
    validate it loads, then re-point ``state.config`` so apps/scenarios come from it."""
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
    state.config = target
    return {"ok": True, "config": str(target), "apps": list_apps(target)}, 200


def set_api_key(state: ServeState, value: str) -> tuple[Any, int]:
    """Set the Claude API key in the serve process's environment for this session (empty clears
    it).  Held in memory only — never written to disk — and inherited by spawned record/run jobs."""
    value = value.strip()
    if value and any(c.isspace() for c in value):
        return {"error": "the API key must not contain whitespace"}, 400
    if value:
        os.environ[_API_KEY_VAR] = value
        return {"ok": True, "set": True, "masked": mask_secret(value)}, 200
    os.environ.pop(_API_KEY_VAR, None)
    return {"ok": True, "set": False}, 200


def set_provider(state: ServeState, body: dict[str, Any]) -> tuple[Any, int]:
    """Select the AI provider for spawned record/crawl jobs: the Anthropic API or Amazon Bedrock.
    Written into the serve process's environment for this session only — never to disk — and
    inherited by jobs, mirroring the API-key handler."""
    prov = str(body.get("provider", "") or "").strip().lower()
    if prov not in PROVIDERS:
        return {"error": f"unknown provider: {prov or '(empty)'}"}, 400
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


def start_run(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("scenario") or not body.get("app"):
        return {"error": "scenario and app are required"}, 400
    app = str(body["app"])
    # Deny an app that belongs to another org (BE-0015 multi-tenancy); single-tenant never forbids.
    if _org_app_forbidden(state, actor, app):
        return {"error": "forbidden"}, 403
    # Confine the scenario to the app's own scenarios dir: a serve client must not be able to run an
    # arbitrary file path on the host (BE-0051 / BE-0015 / BE-0016 prerequisite). The scenario store
    # is scoped to the actor's org so the run reads that org's scenarios.
    scope = state.for_org(_resolve_org(state, actor)).scenarios.scope(app)
    if scope is None:
        return {"error": f"app '{app}' has no scenarios dir"}, 400
    # The store resolves the client value to a trusted runnable — never the client string — so no
    # client-controlled value reaches a filesystem path (BE-0051 arbitrary-path guard). On the
    # server backend it also carries the scenario text as `materials` for a remote worker.
    runnable = scope.runnable(str(body["scenario"]))
    if runnable is None:
        return {"error": "scenario must be an existing .yaml inside the app's scenarios dir"}, 400
    backend = str(body.get("backend", "") or "")
    if backend and not valid_backend(backend):
        return {"error": f"unknown backend: {backend}"}, 400
    udid = str(body.get("udid", "") or "")
    if udid and not valid_udid(udid):
        return {"error": "invalid udid"}, 400
    # When the scenario ships as materials (server backend), the worker has no project on disk, so
    # the config travels too and the run uses workspace-relative paths; locally nothing materializes
    # and the run uses the real config / baselines paths.
    materials = dict(runnable.materials)
    on_worker = bool(materials)
    config_arg = "bajutsu.config.yaml" if on_worker else str(cfg)
    if on_worker:
        materials[config_arg] = cfg.read_text(encoding="utf-8")
    # On the worker, baselines are downloaded into a workspace-relative dir before the run (the
    # control plane's baselines live in object storage); locally the real dir is used directly.
    cmd = run_command(
        runnable.arg,
        app,
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
        dismiss_alerts=body["dismissAlerts"]
        if isinstance(body.get("dismissAlerts"), bool)
        else None,
        config=config_arg,
        baselines="baselines" if on_worker else str(state.baselines_dir),
    )
    app_path, build = app_build_info(cfg, app)
    # Atomic count + create so concurrent dispatches can't both slip past the cap.
    job = state.try_new_job(
        cmd,
        udids=_boot_targets(udid),
        app_path=app_path,
        build=build,
        materials=materials,
        materialize_baselines=on_worker,
        actor=actor,
        org=_resolve_org(state, actor),
    )
    if job is None:
        return {"error": "too many concurrent jobs; try again shortly"}, 429
    state.executor.dispatch(state, job)
    _record_audit(state, actor, "run", f"{app}/{body['scenario']}", {"backend": backend or None})
    return {"jobId": job.id}, 200


def start_record(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Author a scenario from a natural-language goal (the Record tab).  The authored file lands in
    the selected app's configured scenarios dir."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("goal") or not body.get("app"):
        return {"error": "goal and app are required"}, 400
    app = str(body["app"])
    if _org_app_forbidden(state, actor, app):
        return {"error": "forbidden"}, 403
    scope = state.for_org(_resolve_org(state, actor)).scenarios.scope(app)
    if scope is None:
        return {"error": f"app '{body['app']}' has no scenarios dir"}, 400
    authored = scope.authored(str(body.get("name") or "generated"))
    # Validate the device args the same way start_run does (BE-0051): no free-text backend or udid
    # reaches the spawned `bajutsu record` argv. The output path is confined by `authored` above.
    backend = str(body.get("backend", "") or "")
    if backend and not valid_backend(backend):
        return {"error": f"unknown backend: {backend}"}, 400
    udid = str(body.get("udid", "") or "")
    if udid and not valid_udid(udid):
        return {"error": "invalid udid"}, 400
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
        body["app"],
        str(body["goal"]),
        agent=body.get("agent", ""),
        backend=backend,
        udid=udid,
        erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
        dismiss_alerts=body["dismissAlerts"]
        if isinstance(body.get("dismissAlerts"), bool)
        else None,
        config=config_arg,
    )
    app_path, build = app_build_info(cfg, body["app"])
    job = state.try_new_job(
        cmd,
        udids=_boot_targets(udid),
        app_path=app_path,
        build=build,
        out_path=authored.out,
        materials=materials,
        record_save=authored.save,
        actor=actor,
        org=_resolve_org(state, actor),
    )
    if job is None:
        return {"error": "too many concurrent jobs; try again shortly"}, 429
    state.executor.dispatch(state, job)
    _record_audit(state, actor, "record", str(body["app"]), {"goal": str(body["goal"])})
    # Report the saved ref on the server (what the UI loads), else the on-disk path.
    return {"jobId": job.id, "path": authored.save[1] if authored.save else authored.out}, 200


def start_crawl(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Explore an app breadth-first and build a screen map (the Crawl tab).  The screen map is
    streamed into ``runs/<runId>/screenmap.json``; the returned ``runId`` lets the UI poll it."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("app"):
        return {"error": "app is required"}, 400
    if _org_app_forbidden(state, actor, str(body["app"])):
        return {"error": "forbidden"}, 403
    # Resume continues an existing run (a pruned branch tapped in the UI); otherwise a new run.
    resume_src = str(body.get("resumeSrc", "") or "")
    resume_key = str(body.get("resumeKey", "") or "")
    resuming = bool(resume_src and resume_key and body.get("runId"))
    run_id = str(body["runId"]) if resuming else datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    # A resumed crawl takes runId from the client; reject anything but a safe path segment so
    # `runs_dir / run_id` (the crawl's --out) can't escape runs_dir (BE-0051).
    if resuming and not valid_run_id(run_id):
        return {"error": "invalid runId"}, 400
    backend = str(body.get("backend", "") or "")
    if backend and not valid_backend(backend):
        return {"error": f"unknown backend: {backend}"}, 400
    udid = str(body.get("udid", "") or "")
    if udid and not valid_udid(udid):
        return {"error": "invalid udid"}, 400
    cmd = crawl_command(
        str(body["app"]),
        out=str(state.runs_dir / run_id),
        agent=body.get("agent", ""),
        backend=backend,
        udid=udid,
        max_screens=_int(body.get("maxScreens"), 50),
        max_steps=_int(body.get("maxSteps"), 200),
        erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
        dismiss_alerts=body["dismissAlerts"]
        if isinstance(body.get("dismissAlerts"), bool)
        else None,
        config=str(cfg),
        resume_src=resume_src if resuming else "",
        resume_key=resume_key if resuming else "",
    )
    app_path, build = app_build_info(cfg, str(body["app"]))
    # Cap concurrency like run/record: crawl is long and device-heavy (BE-0051 slice 5).
    job = state.try_new_job(
        cmd,
        udids=_boot_targets(udid),
        app_path=app_path,
        build=build,
        actor=actor,
        org=_resolve_org(state, actor),
    )
    if job is None:
        return {"error": "too many concurrent jobs; try again shortly"}, 429
    state.executor.dispatch(state, job)
    _record_audit(state, actor, "crawl", str(body["app"]), {"runId": run_id})
    return {"jobId": job.id, "runId": run_id}, 200


def save_scenario(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Save an edited scenario back to its ``*.yaml`` (bounded to the app's scenarios dir)."""
    app = str(body.get("app") or "") or None
    # Deny saving into another org's app (BE-0015 multi-tenancy); single-tenant never forbids.
    if app is not None and _org_app_forbidden(state, actor, app):
        return {"error": "forbidden"}, 403
    # Resolve the scope and screen the ref before parsing: a non-saveable path is reported ahead of
    # a YAML error (the local store passes an absolute path inside its dir).
    scope = state.for_org(_resolve_org(state, actor)).scenarios.scope(app)
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
    bundle = state.for_org(_resolve_org(state, actor))
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

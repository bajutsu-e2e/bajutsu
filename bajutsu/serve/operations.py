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
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bajutsu.agents import AGENT_ENV
from bajutsu.anthropic_client import (
    BEDROCK_MODEL_ENV,
    PROVIDER_ENV,
    PROVIDERS,
    provider,
)
from bajutsu.config import load_config, resolve, targets_for_org
from bajutsu.config_source import materialize, parse_config_spec, source_provenance
from bajutsu.scenario import load_scenario_file
from bajutsu.serve import jobs

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

# The one secret the WebUI lets you set; the AI paths (record, --dismiss-alerts) read it.
_API_KEY_VAR = "ANTHROPIC_API_KEY"


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
    key = os.environ.get(_API_KEY_VAR) or None
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
    return {
        "provider": mode,
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
        return [r.summary for r in state.repository.list_runs(org_id=state.org_of(actor))], 200
    return state.artifacts.list_runs(), 200


def read_scenario(
    state: ServeState, target: str | None, path: str | None, *, actor: str | None = None
) -> tuple[Any, int]:
    # A scenario in another org's target reads as not-found (non-leaky) — BE-0015 multi-tenancy.
    org = state.org_of(actor)
    if target is not None and _target_forbidden(state, org, target):
        return {"error": "not found"}, 404
    scope = state.for_org(org).scenarios.scope(target)
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
        return None, ({"error": "too many concurrent jobs; try again shortly"}, 429)
    state.executor.dispatch(state, registered)
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
        baselines="baselines" if on_worker else str(state.baselines_dir),
        headed=_bool_flag(body, "headed"),
    )
    app_path, build = target_build_info(cfg, target)
    job, capped = _register_and_dispatch(
        state,
        Job(
            cmd=cmd,
            udids=_boot_targets(udid),
            app_path=app_path,
            build=build,
            materials=materials,
            materialize_baselines=on_worker,
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

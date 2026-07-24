"""The declarative route registry both serve backends dispatch from (BE-0253).

`serve` runs two HTTP backends behind one API surface — a stdlib `http.server` handler
(`handler.py`) and a FastAPI app (`server/app.py`). Historically each declared every endpoint
twice: once as a `match path` case in the stdlib handler and once as an `@app.<method>` route,
both dispatching to the same `bajutsu.serve.operations`. The two hand-maintained tables drifted
silently (endpoints present in one backend, missing in the other).

This module is the single source of truth: one `ROUTES` table each backend iterates. An entry
carries the HTTP method, the path pattern (FastAPI-style templates like `/api/jobs/{job_id}` —
the spelling FastAPI consumes directly), and, for the *uniform* routes, a backend-neutral
`handle(state, ctx)` adapter that captures the `ops.*` call once. Each backend supplies its own
`ctx` (how a request's query/body/path-params/actor are read) and its own response writer, so the
transport mechanics stay per-backend while the route table is shared.

`off_loop` routes (SSE, file/range serving, raw-body uploads, the OAuth round-trip, login, and the
index render) write their own responses and differ structurally per backend, so the registry only
*declares* them (`handle=None`) — each backend keeps its bespoke handling. `local_only` marks a
route the FastAPI generator deliberately skips — Part 4's triage marks `/api/ant/login` and
`/api/capture/*`; every other route is served by both backends. `content_type`, when set, selects a
text response over JSON.

Framework-agnostic by construction — like `gate.py`, it must import without FastAPI so the default
stdlib serve path stays lean (`tests/serve/test_import_guard.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from bajutsu.serve import operations as ops
from bajutsu.serve.state import ServeState

# Content type for the HTML dashboard routes (/stats, /flakiness, /usage); /metrics uses the
# Prometheus type. Both drive `_text` instead of the JSON writer.
_HTML = "text/html; charset=utf-8"


class RequestCtx(Protocol):
    """Backend-neutral view of one request, read by a route's `handle` adapter.

    The stdlib `Handler` and a FastAPI shim both satisfy this, so one adapter closure serves both
    backends.
    """

    def path_param(self, name: str) -> str:
        """The URL-decoded value bound to a `{name}` segment of the matched path template.

        Both backends return the decoded segment: the stdlib ctx `unquote`s the raw match, the
        FastAPI ctx passes Starlette's already-decoded param. A closure therefore never decodes,
        so the two backends can't drift on how a percent-encoded segment reaches its `ops` call.
        """
        ...

    def query(self, key: str) -> str | None:
        """The first value of query parameter *key*, or None."""
        ...

    def body(self) -> dict[str, Any]:
        """The parsed JSON request body (an empty dict for a GET)."""
        ...

    def actor(self) -> str | None:
        """The GitHub login bound to this request's session, or None."""
        ...


# A uniform route's adapter: extract this route's arguments from the request and call its `ops`
# function, returning the `(payload, status)` pair the backend writes as JSON (or text).
Handle = Callable[[ServeState, RequestCtx], tuple[Any, int]]


@dataclass(frozen=True)
class Route:
    """One endpoint's declaration, shared by both backends.

    Attributes:
        method: HTTP method — "GET", "POST", or "DELETE".
        path: FastAPI-style path template (e.g. "/api/projects/{name}/runs").
        handle: The uniform `(state, ctx) -> (payload, status)` adapter, or None for an
            `off_loop` route each backend handles bespoke.
        off_loop: The route writes its own response (streaming, file serve, raw upload, redirect)
            rather than the uniform JSON/text path; declared here but dispatched per backend.
        local_only: The FastAPI generator deliberately skips this route (Part 4's triage:
            `/api/ant/login`, `/api/capture/*`).
        content_type: When set, the response is text of this type instead of JSON.
    """

    method: str
    path: str
    handle: Handle | None = None
    off_loop: bool = False
    local_only: bool = False
    content_type: str | None = None


def _match_template(template: str, path: str) -> dict[str, str] | None:
    """Match a concrete *path* against one FastAPI-style *template*, returning the bound path
    parameters (raw, not URL-decoded) or None. A `{name}` binds exactly one segment; a
    `{name:path}` (only valid as the final segment) binds the greedy remainder including slashes."""
    if "{" not in template:  # exact route — the common case
        return {} if template == path else None
    t_segs = template.split("/")
    p_segs = path.split("/")
    params: dict[str, str] = {}
    for i, seg in enumerate(t_segs):
        if seg.startswith("{") and seg.endswith("}"):
            name = seg[1:-1]
            if name.endswith(":path"):  # greedy remainder, must be the last template segment
                params[name[: -len(":path")]] = "/".join(p_segs[i:])
                return params
            if i >= len(p_segs):
                return None
            params[name] = p_segs[i]
        elif i >= len(p_segs) or p_segs[i] != seg:
            return None
    return params if len(p_segs) == len(t_segs) else None


def match_route(
    routes: Sequence[Route], method: str, path: str
) -> tuple[Route, dict[str, str]] | None:
    """The first route matching *method* and *path*, with its bound path parameters, or None.

    List order is precedence: a route listed before another that could also match a given path
    wins (e.g. `/runs/{run_id}/archive.zip` before the greedy `/runs/{rel:path}`)."""
    for route in routes:
        if route.method != method:
            continue
        params = _match_template(route.path, path)
        if params is not None:
            return route, params
    return None


# The route table. Order is load-bearing for the greedy `/runs/{rel:path}`, which must come after
# the more specific `/runs/{run_id}/archive.zip` it would otherwise swallow; the exact-segment-count
# matcher keeps every other pair order-independent. `off_loop` entries carry no handle — each
# backend dispatches them bespoke (see the module docstring).
ROUTES: tuple[Route, ...] = (
    # --- GET: streaming / binary (off_loop) ---
    Route("GET", "/api/jobs/{job_id}/events", off_loop=True),
    Route("GET", "/runs/{run_id}/archive.zip", off_loop=True),
    Route("GET", "/api/capture/screenshot", off_loop=True, local_only=True),
    Route("GET", "/runs/{rel:path}", off_loop=True),
    # --- GET: index (off_loop) ---
    Route("GET", "/", off_loop=True),
    Route("GET", "/index.html", off_loop=True),
    # --- GET: uniform JSON reads ---
    Route(
        "GET",
        "/api/scenarios",
        lambda state, ctx: ops.list_scenarios(state, ctx.query("target"), actor=ctx.actor()),
    ),
    Route(
        "GET", "/api/targets", lambda state, ctx: ops.list_targets_payload(state, actor=ctx.actor())
    ),
    # Running-tool identity (BE-0272): version is open; the Git checkout detail is admin-gated
    # (see `authz.required_role`) because a branch name can encode an in-progress topic.
    Route("GET", "/api/version", lambda state, ctx: ops.server_version()),
    Route("GET", "/api/version/checkout", lambda state, ctx: ops.server_checkout()),
    Route("GET", "/api/config", lambda state, ctx: ops.config_info(state)),
    Route("GET", "/api/config/content", lambda state, ctx: ops.config_content(state)),
    # The running server's resolved configuration + the bundled iOS runner state (BE-0318). Read-only
    # and open like /api/config; the operation withholds host paths when hosted (BE-0108).
    Route("GET", "/api/server", lambda state, ctx: ops.server_settings(state)),
    Route("GET", "/api/fs", lambda state, ctx: ops.browse_fs(state, ctx.query("dir"))),
    Route("GET", "/api/apikey", lambda state, ctx: ops.api_key_info(state, ctx.actor())),
    Route(
        "GET",
        "/api/claudecodetoken",
        lambda state, ctx: ops.claude_code_token_info(state, ctx.actor()),
    ),
    Route(
        "GET", "/api/gitcredential", lambda state, ctx: ops.git_credential_info(state, ctx.actor())
    ),
    # The scenario secrets the bound config declares (BE-0274): describe-only (masked, no value),
    # so — like the three credential reads above — it carries no role gate.
    Route("GET", "/api/secrets", lambda state, ctx: ops.scenario_secrets_info(state, ctx.actor())),
    Route("GET", "/api/provider", lambda state, ctx: ops.provider_info(state, ctx.actor())),
    Route("GET", "/api/themecontract", lambda state, ctx: ops.get_theme_contract(state)),
    Route(
        "GET",
        "/api/ant/login",
        lambda state, ctx: ops.ant_login_status(state),
        local_only=True,
    ),
    Route("GET", "/api/simulators", lambda state, ctx: ops.simulators_payload(state)),
    Route(
        "GET",
        "/api/runs",
        lambda state, ctx: ops.runs_payload(
            state, actor=ctx.actor(), scenario=ctx.query("scenario")
        ),
    ),
    Route(
        "GET", "/api/projects", lambda state, ctx: ops.list_projects_view(state, actor=ctx.actor())
    ),
    Route(
        "GET",
        "/api/projects/{name}/runs",
        lambda state, ctx: ops.project_runs(state, ctx.path_param("name"), actor=ctx.actor()),
    ),
    Route(
        "GET",
        "/api/metrics/projects",
        lambda state, ctx: ops.project_metrics_view(state, actor=ctx.actor()),
    ),
    Route(
        "GET",
        "/api/crawl/runs",
        lambda state, ctx: ops.crawl_runs_payload(state, actor=ctx.actor()),
    ),
    # Static path; the exact-segment matcher can't confuse `/api/runs/trash` (3 segments) with the
    # `/api/runs/{run_id}/…` templates (4+), and there is no GET `/api/runs/{run_id}` to shadow it.
    Route(
        "GET",
        "/api/runs/trash",
        lambda state, ctx: ops.trashed_runs_payload(state, actor=ctx.actor()),
    ),
    Route(
        "GET",
        "/api/artifacts/exists",
        lambda state, ctx: ops.artifact_exists(
            state, ctx.query("kind"), ctx.query("sha256"), actor=ctx.actor()
        ),
    ),
    Route(
        "GET",
        "/api/scenario",
        lambda state, ctx: ops.read_scenario(
            state,
            ctx.query("target"),
            ctx.query("path"),
            actor=ctx.actor(),
            run_id=ctx.query("runId"),
            scenario_name=ctx.query("scenario"),
            structure=ctx.query("structure") == "1",
        ),
    ),
    Route("GET", "/api/schema", lambda state, ctx: ops.scenario_schema()),
    Route(
        "GET",
        "/api/jobs/{job_id}",
        lambda state, ctx: ops.job_view(state, ctx.path_param("job_id")),
    ),
    # --- GET: text responses (content_type) ---
    Route(
        "GET",
        "/metrics",
        lambda state, ctx: ops.render_metrics(state),
        content_type=ops.PROMETHEUS_CONTENT_TYPE,
    ),
    Route(
        "GET",
        "/stats",
        lambda state, ctx: ops.stats_html(state, actor=ctx.actor()),
        content_type=_HTML,
    ),
    Route(
        "GET",
        "/flakiness",
        lambda state, ctx: ops.flakiness_html(state, actor=ctx.actor()),
        content_type=_HTML,
    ),
    Route(
        "GET",
        "/usage",
        lambda state, ctx: ops.usage_html(state, actor=ctx.actor()),
        content_type=_HTML,
    ),
    # --- GET: OAuth round-trip (off_loop) ---
    Route("GET", "/api/oauth/login", off_loop=True),
    Route("GET", "/api/oauth/callback", off_loop=True),
    # --- POST: raw-body uploads (off_loop) ---
    Route("POST", "/api/upload", off_loop=True),
    Route("POST", "/api/artifacts/config", off_loop=True),
    Route("POST", "/api/artifacts/scenarios", off_loop=True),
    Route("POST", "/api/artifacts/binary", off_loop=True),
    # --- POST: login (off_loop, sets the session cookie) ---
    Route("POST", "/api/login", off_loop=True),
    # --- POST: uniform JSON actions ---
    Route(
        "POST",
        "/api/config",
        # A `git` key selects the from-Git picker (BE-0063); `path` the local browser. Key presence
        # (not truthiness) routes, so an empty `git` still reaches the Git binder's 400.
        lambda state, ctx: (
            ops.bind_git_config(state, str(ctx.body().get("git") or ""))
            if "git" in ctx.body()
            else ops.bind_config(state, str(ctx.body().get("path", "") or ""))
        ),
    ),
    Route(
        "POST",
        "/api/apikey",
        lambda state, ctx: ops.set_api_key(
            state, str(ctx.body().get("value", "") or ""), ctx.actor()
        ),
    ),
    Route(
        "POST",
        "/api/claudecodetoken",
        lambda state, ctx: ops.set_claude_code_token(
            state, str(ctx.body().get("value", "") or ""), ctx.actor()
        ),
    ),
    Route(
        "POST",
        "/api/gitcredential",
        lambda state, ctx: ops.set_git_credential(
            state, str(ctx.body().get("value", "") or ""), ctx.actor()
        ),
    ),
    Route(
        "POST", "/api/provider", lambda state, ctx: ops.set_provider(state, ctx.body(), ctx.actor())
    ),
    # Set/clear a scenario-declared secret (BE-0274): admin-gated (see `authz._ADMIN_PATHS`),
    # rejects any name the bound config doesn't declare.
    Route(
        "POST",
        "/api/secrets",
        lambda state, ctx: ops.set_scenario_secret(state, ctx.body(), ctx.actor()),
    ),
    Route(
        "POST", "/api/theme", lambda state, ctx: ops.upload_theme(state, ctx.body(), ctx.actor())
    ),
    Route(
        "POST",
        "/api/compose",
        lambda state, ctx: ops.bind_composition(state, ctx.body(), actor=ctx.actor()),
    ),
    Route("POST", "/api/ant/login", lambda state, ctx: ops.ant_login(state), local_only=True),
    Route(
        "POST", "/api/run", lambda state, ctx: ops.start_run(state, ctx.body(), actor=ctx.actor())
    ),
    Route(
        "POST",
        "/api/projects",
        lambda state, ctx: ops.register_project(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/projects/{name}/run",
        lambda state, ctx: ops.run_project(
            state, ctx.path_param("name"), ctx.body(), actor=ctx.actor()
        ),
    ),
    Route(
        "POST",
        "/api/projects/{name}/activate",
        lambda state, ctx: ops.activate_project(state, ctx.path_param("name"), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/record",
        lambda state, ctx: ops.start_record(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/crawl",
        lambda state, ctx: ops.start_crawl(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/triage",
        lambda state, ctx: ops.start_triage(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/scenario",
        lambda state, ctx: ops.save_scenario(state, ctx.body(), actor=ctx.actor()),
    ),
    Route("POST", "/api/lint", lambda state, ctx: ops.lint_scenario(ctx.body())),
    Route(
        "POST",
        "/api/scenario/apply-selector",
        lambda state, ctx: ops.apply_selector_edit(ctx.body()),
    ),
    Route(
        "POST",
        "/api/scenario/enrich-apply",
        lambda state, ctx: ops.apply_enrichment_edit(ctx.body()),
    ),
    Route(
        "POST",
        "/api/audit",
        lambda state, ctx: ops.audit_scenario(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/codegen",
        lambda state, ctx: ops.generate_codegen(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/approve",
        lambda state, ctx: ops.approve_baseline(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/scenario/resolve",
        lambda state, ctx: ops.resolve_scenario_pick(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/enrich",
        lambda state, ctx: ops.start_enrich(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/doctor",
        lambda state, ctx: ops.doctor_check(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/coverage",
        lambda state, ctx: ops.coverage_view(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/capture/start",
        lambda state, ctx: ops.start_capture(state, ctx.body(), actor=ctx.actor()),
        local_only=True,
    ),
    Route(
        "POST",
        "/api/capture/mark",
        lambda state, ctx: ops.mark_capture(state, ctx.body(), actor=ctx.actor()),
        local_only=True,
    ),
    Route(
        "POST",
        "/api/capture/finish",
        lambda state, ctx: ops.finish_capture(state, ctx.body(), actor=ctx.actor()),
        local_only=True,
    ),
    # Live step-picking for the Edit editor (BE-0262): resolve reuses the capture session's live
    # tree without actuating (pure authoring assist), close ends it without saving a scenario.
    Route(
        "POST",
        "/api/capture/resolve",
        lambda state, ctx: ops.resolve_capture_pick(state, ctx.body(), actor=ctx.actor()),
        local_only=True,
    ),
    Route(
        "POST",
        "/api/capture/close",
        lambda state, ctx: ops.close_capture(state, ctx.body(), actor=ctx.actor()),
        local_only=True,
    ),
    Route(
        "POST",
        "/api/worker/lease",
        lambda state, ctx: ops.worker_lease(
            state, ctx.body().get("worker_id", ""), ctx.body().get("capabilities")
        ),
    ),
    Route(
        "POST",
        "/api/worker/heartbeat",
        lambda state, ctx: ops.worker_heartbeat(
            state, ctx.body().get("worker_id", ""), ctx.body().get("job_id", "")
        ),
    ),
    Route("POST", "/api/worker/result", lambda state, ctx: ops.worker_result(state, ctx.body())),
    Route(
        "POST",
        "/api/worker/artifact-urls",
        lambda state, ctx: ops.worker_artifact_urls(state, ctx.body()),
    ),
    Route(
        "POST",
        "/api/worker/scenario-url",
        lambda state, ctx: ops.worker_scenario_url(state, ctx.body()),
    ),
    Route(
        "POST",
        "/api/jobs/{job_id}/cancel",
        lambda state, ctx: ops.cancel_job(state, ctx.path_param("job_id")),
    ),
    Route(
        "POST",
        "/api/jobs/{job_id}/respond-human",
        lambda state, ctx: ops.respond_human(state, ctx.path_param("job_id"), ctx.body()),
    ),
    Route(
        "POST",
        "/api/runs/{run_id}/upload-urls",
        lambda state, ctx: ops.generate_upload_urls(state, ctx.path_param("run_id"), ctx.body()),
    ),
    # Static path; today's exact-segment-count matcher can't confuse it with the `{run_id}`
    # templates below (4 segments vs. 5), but it's kept first for readability and in case a
    # same-length template is ever added here.
    Route(
        "POST",
        "/api/runs/bulk-delete",
        lambda state, ctx: ops.bulk_delete_runs(state, ctx.body(), actor=ctx.actor()),
    ),
    Route(
        "POST",
        "/api/runs/{run_id}/restore",
        lambda state, ctx: ops.restore_run(state, ctx.path_param("run_id"), actor=ctx.actor()),
    ),
    # --- DELETE ---
    Route(
        "DELETE",
        "/api/crawl/runs/{run_id}",
        lambda state, ctx: ops.delete_run(
            state,
            ctx.path_param("run_id"),
            purge=ctx.query("purge") == "true",
            actor=ctx.actor(),
        ),
    ),
    Route(
        "DELETE",
        "/api/runs/{run_id}",
        lambda state, ctx: ops.delete_run(
            state,
            ctx.path_param("run_id"),
            purge=ctx.query("purge") == "true",
            actor=ctx.actor(),
        ),
    ),
    Route(
        "DELETE",
        "/api/projects/{name}",
        lambda state, ctx: ops.deregister_project(state, ctx.path_param("name"), actor=ctx.actor()),
    ),
)

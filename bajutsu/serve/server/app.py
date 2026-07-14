"""The hosted-backend control plane: a FastAPI app over the serve seams (BE-0015 server phase).

`make_app(state)` serves the **same** SPA + API as the local stdlib handler, delegating every
request to the shared `bajutsu.serve.operations` — so the local and hosted backends stay in
lockstep and only differ in which seam implementations the `ServeState` carries. This module is
imported only when the server backend is selected (the import guard in
``tests/serve/test_import_guard.py`` keeps it off the default path); FastAPI lives in the
``server`` optional-dependency group.

The transport-specific parts mirror the stdlib handler one-to-one: the auth gate (BE-0051), the
unconditional CSRF Origin check and Host allowlist (BE-0121), the session cookie, and the hardening
response headers. Live-log SSE streaming arrives with a later slice; the rest of the API surface is here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.requests import ClientDisconnect

from bajutsu.serve import operations as ops
from bajutsu.serve import oplog
from bajutsu.serve.handler import _OAUTH_STATE_COOKIE, _SESSION_COOKIE, _index_html
from bajutsu.serve.helpers import range_reply
from bajutsu.serve.state import ServeState
from bajutsu.serve.upload_artifacts import ArtifactKind
from bajutsu.serve.uploads import MAX_UPLOAD_BYTES, BoundedZipReceiver, UploadTooLarge

# How long an idle SSE stream waits before sending a `:keepalive` comment (and rechecking for a
# client disconnect). Short enough to stay under a reverse proxy's idle timeout (BE-0015).
_SSE_KEEPALIVE = 15.0

# Returned by `next(..., _STREAM_DONE)` at exhaustion instead of raising StopIteration (which can't
# cross the threadpool/coroutine boundary cleanly).
_STREAM_DONE: Any = object()

# Endpoints reachable without a credential when a token is configured (mirrors the stdlib gate):
# the index page so the login UI can load, and the login endpoint itself.
_OPEN_GET = ("/", "/index.html", "/api/oauth/login", "/api/oauth/callback")
_LOGIN_PATH = "/api/login"
_OAUTH_LOGIN = "/api/oauth/login"
_OAUTH_CALLBACK = "/api/oauth/callback"


def _result(payload_status: tuple[Any, int]) -> JSONResponse:
    payload, status = payload_status
    return JSONResponse(payload, status_code=status)


def make_app(state: ServeState) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _authorized(request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and state.auth.check_token(auth[len("Bearer ") :]):
            return True
        sid = request.cookies.get(_SESSION_COOKIE)
        return sid is not None and state.auth.valid_session(sid)

    def _actor(request: Request) -> str | None:
        # The GitHub login bound to this request's session, for audit attribution (BE-0015 7c);
        # None for a token/Bearer request or no session. Mirrors the stdlib handler's `_actor`.
        sid = request.cookies.get(_SESSION_COOKIE)
        return state.auth.sessions.identity(sid) if sid else None

    @app.middleware("http")
    async def gate(request: Request, call_next: Any) -> Response:
        """Host allowlist + auth + CSRF + hardening headers, mirroring the stdlib handler's
        `_gate`/`_host_ok`/`_csrf_ok`/`end_headers` exactly so the two backends enforce the same
        policy (BE-0051/BE-0121)."""
        oplog.bind_request(oplog.new_request_id())
        # DNS-rebinding defense (BE-0121): a Host that names no bound interface is refused ahead of
        # everything else, regardless of the token/CSRF posture. An empty allowlist (a wildcard bind,
        # set by make_asgi_server) accepts any Host, so this is off unless we bound a named interface.
        if state.allowed_hosts:
            host = urlparse(f"//{request.headers.get('host', '')}").hostname
            if host not in state.allowed_hosts:
                return _hardened(JSONResponse({"error": "host not allowed"}, status_code=403))
        # Block cross-origin state-changing requests unconditionally (BE-0121) — not only when a token
        # is configured. A no-token server would otherwise leave every POST as an unguarded CSRF
        # surface (the CSRF-to-arbitrary-config hole this closes). DELETE (deregister a project,
        # BE-0225) is equally state-changing, so it is guarded too. No Origin (a non-browser client)
        # passes, matching the stdlib `_csrf_ok`.
        if request.method in ("POST", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != (request.headers.get("host") or ""):
                return _hardened(
                    JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
                )
        if state.auth.token is not None:
            path, method = request.url.path, request.method
            open_path = (method == "GET" and path in _OPEN_GET) or (
                method == "POST" and path == _LOGIN_PATH
            )
            if not open_path and not _authorized(request):
                return _hardened(JSONResponse({"error": "unauthorized"}, status_code=401))
            # Enforce the user's role on mutating endpoints for an OAuth session (an identity) when a
            # database is wired (BE-0015 7c-2); token/Bearer has no identity and stays full-access.
            login = _actor(request)
            if (
                login is not None
                and state.repository is not None
                and ops.forbidden_for_role(state, login, method, path)
            ):
                return _hardened(JSONResponse({"error": "forbidden"}, status_code=403))
        return _hardened(await call_next(request))

    def _hardened(response: Response) -> Response:
        # Standard hardening headers on every response (BE-0051): block MIME sniffing and
        # cross-origin framing (clickjacking), and don't leak the URL via Referer. SAMEORIGIN (not
        # DENY) so the Replay view can frame its own run report (/runs/<id>/report.html).
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # --- SPA + static run artifacts ---

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        # Lockstep with the stdlib handler: forward the drop-in themes dir + default (BE-0191).
        return HTMLResponse(_index_html(state.themes_dir, state.default_theme))

    @app.get("/runs/{rel:path}")
    async def run_file(rel: str, request: Request) -> Response:
        # The actor's org-scoped artifact store: a run in another org's prefix reads as not-found
        # (BE-0015 multi-tenancy). report.html renders on view from the stored model (BE-0068).
        art = ops.run_file(state.for_org(state.org_of(_actor(request))).artifacts, rel)
        if art is None:
            return _result(({"error": "not found"}, 404))
        if art.redirect is not None:  # a server store hands back a signed URL
            return RedirectResponse(art.redirect, status_code=302)
        # Honor a `Range` request: a report's `<video>` needs 206/`Content-Range` replies to seek
        # into a scrub target the browser hasn't buffered yet.
        status, chunk, headers = range_reply(art.body or b"", request.headers.get("range"))
        if status == 416:
            return Response(status_code=416, headers=headers)
        return Response(chunk, status_code=status, media_type=art.content_type, headers=headers)

    # --- GET (reads) ---

    @app.get("/api/scenarios")
    async def scenarios(request: Request, target: str | None = None) -> JSONResponse:
        return _result(ops.list_scenarios(state, target, actor=_actor(request)))

    @app.get("/api/targets")
    async def targets(request: Request) -> JSONResponse:
        return _result(ops.list_targets_payload(state, actor=_actor(request)))

    @app.get("/api/config")
    async def config() -> JSONResponse:
        return _result(ops.config_info(state))

    @app.get("/api/config/content")
    async def config_content() -> JSONResponse:
        return _result(ops.config_content(state))

    @app.get("/api/fs")
    async def fs(dir: str | None = None) -> JSONResponse:
        return _result(ops.browse_fs(state, dir))

    @app.get("/api/apikey")
    async def api_key(request: Request) -> JSONResponse:
        return _result(ops.api_key_info(state, _actor(request)))

    @app.get("/api/claudecodetoken")
    async def claude_code_token(request: Request) -> JSONResponse:
        return _result(ops.claude_code_token_info(state, _actor(request)))

    @app.get("/api/gitcredential")
    async def git_credential(request: Request) -> JSONResponse:
        return _result(ops.git_credential_info(state, _actor(request)))

    @app.get("/api/provider")
    async def get_provider(request: Request) -> JSONResponse:
        return _result(ops.provider_info(state, _actor(request)))

    @app.get("/api/themecontract")
    async def theme_contract() -> JSONResponse:
        return _result(ops.get_theme_contract(state))

    @app.get("/api/simulators")
    async def simulators() -> JSONResponse:
        return _result(ops.simulators_payload(state))

    @app.get("/api/runs")
    async def runs(request: Request) -> JSONResponse:
        return _result(ops.runs_payload(state, actor=_actor(request)))

    @app.get("/api/crawl/runs")
    async def crawl_runs(request: Request) -> JSONResponse:
        return _result(ops.crawl_runs_payload(state, actor=_actor(request)))

    @app.get("/api/projects")
    async def list_projects(request: Request) -> JSONResponse:
        return _result(ops.list_projects_view(state, actor=_actor(request)))

    @app.get("/api/projects/{name}/runs")
    async def project_runs(name: str, request: Request) -> JSONResponse:
        return _result(ops.project_runs(state, name, actor=_actor(request)))

    @app.get("/api/metrics/projects")
    async def project_metrics(request: Request) -> JSONResponse:
        return _result(ops.project_metrics_view(state, actor=_actor(request)))

    @app.get("/stats", response_class=HTMLResponse)
    async def stats(request: Request) -> HTMLResponse:
        html, code = ops.stats_html(state, actor=_actor(request))
        return HTMLResponse(html, status_code=code)

    @app.get("/usage", response_class=HTMLResponse)
    async def usage(request: Request) -> HTMLResponse:
        html, code = ops.usage_html(state, actor=_actor(request))
        return HTMLResponse(html, status_code=code)

    @app.get("/metrics")
    async def metrics() -> Response:
        # Prometheus text format; behind the same auth gate as every route (BE-0051), so a scraper
        # authenticates with the operator token when one is configured.
        text, code = ops.render_metrics(state)
        return Response(text, status_code=code, media_type=ops.PROMETHEUS_CONTENT_TYPE)

    @app.get("/api/scenario")
    async def read_scenario(
        request: Request,
        target: str | None = None,
        path: str | None = None,
        runId: str | None = None,  # noqa: N803
        scenario: str | None = None,
    ) -> JSONResponse:
        return _result(
            ops.read_scenario(
                state,
                target,
                path,
                actor=_actor(request),
                run_id=runId,
                scenario_name=scenario,
            )
        )

    @app.get("/api/schema")
    async def scenario_schema() -> JSONResponse:
        return _result(ops.scenario_schema())

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str) -> JSONResponse:
        return _result(ops.job_view(state, job_id))

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> Response:
        frames = ops.job_sse(state, job_id, keepalive=_SSE_KEEPALIVE)
        if frames is None:
            return _result(({"error": "no such job"}, 404))

        # The shared frame stream blocks (LogBus.stream), so pull each frame in a threadpool to keep
        # the event loop free. The stream's keepalive timeout bounds each pull, so between frames we
        # can check for a client disconnect and stop — freeing the worker thread within one
        # keepalive interval instead of parking it until the job ends. X-Accel-Buffering off so a
        # proxy doesn't buffer the stream.
        async def body() -> AsyncIterator[str]:
            # `next(it, _DONE)` returns the sentinel at exhaustion rather than raising StopIteration,
            # which can't cross the threadpool/coroutine boundary cleanly.
            while True:
                if await request.is_disconnected():
                    frames.close()
                    return
                frame = await run_in_threadpool(next, frames, _STREAM_DONE)
                if frame is _STREAM_DONE:
                    return
                yield frame

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- POST (actions) ---

    @app.post(_LOGIN_PATH)
    async def login(body: dict[str, Any]) -> JSONResponse:
        payload, status, sid = ops.login(state, str(body.get("token", "") or ""))
        resp = JSONResponse(payload, status_code=status)
        if sid is not None:
            resp.set_cookie(_SESSION_COOKIE, sid, httponly=True, samesite="strict", path="/")
        return resp

    @app.get(_OAUTH_LOGIN)
    async def oauth_login() -> Response:
        # Mirror of the stdlib handler's `_oauth_login`: redirect to GitHub, stash the CSRF state in
        # a short-lived SameSite=Lax cookie (so it survives the redirect back).
        payload, status, csrf = ops.oauth_login(state)
        if status != 200 or csrf is None:
            return JSONResponse(payload, status_code=status)
        resp = RedirectResponse(payload["redirect"], status_code=302)
        resp.set_cookie(
            _OAUTH_STATE_COOKIE, csrf, httponly=True, samesite="lax", path="/", max_age=600
        )
        return resp

    @app.get(_OAUTH_CALLBACK)
    async def oauth_callback(request: Request) -> Response:
        # `state` is the OAuth query param; read it via query_params to avoid shadowing the
        # ServeState closure. On success: session cookie + clear the state cookie + land on the app.
        # oauth_callback exchanges the code with GitHub (sync network I/O); run it off the event
        # loop so a slow GitHub call can't block other requests (mirrors the SSE pull).
        payload, status, sid = await run_in_threadpool(
            ops.oauth_callback,
            state,
            request.query_params.get("code", ""),
            request.query_params.get("state", ""),
            request.cookies.get(_OAUTH_STATE_COOKIE, ""),
        )
        if status != 200 or sid is None:
            return JSONResponse(payload, status_code=status)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(_SESSION_COOKIE, sid, httponly=True, samesite="strict", path="/")
        resp.delete_cookie(_OAUTH_STATE_COOKIE, path="/")
        return resp

    @app.post("/api/config")
    async def bind_config(body: dict[str, Any]) -> JSONResponse:
        # A `git` key selects the from-Git picker (BE-0063); `path` the local file browser. Key
        # presence (not truthiness) routes, so an empty `git` still reaches the Git binder's 400. The
        # Git path does blocking network I/O (materialize), so run it off the event loop.
        if "git" in body:
            return _result(
                await run_in_threadpool(ops.bind_git_config, state, str(body.get("git") or ""))
            )
        return _result(ops.bind_config(state, str(body.get("path", "") or "")))

    async def _stream_bounded_body(
        request: Request,
    ) -> BoundedZipReceiver | tuple[Any, int]:
        """Stream a raw POST body into a bounded, sha256-hashing temp file (`BoundedZipReceiver`,
        shared by `/api/upload` (BE-0073) and the `/api/artifacts/*` routes (BE-0268)) — the size cap
        is enforced both up front (Content-Length) and while reading, so a lying length can't overrun
        it. CSRF/Origin is already enforced unconditionally by the `gate` middleware above, so this
        only needs the size cap and the streamed read. Returns the receiver on success, or an
        `(error, status)` pair the caller passes straight to `_result` on any failure — a missing/
        oversized Content-Length, a short read, or a mid-stream cap/disconnect/OS error — cleaning the
        receiver up itself in that case; a caller only gets a receiver back on success, and owns its
        `cleanup()` from there."""
        length = int(request.headers.get("content-length") or 0)
        if length <= 0:
            return {"error": "empty upload"}, 400
        if length > MAX_UPLOAD_BYTES:
            return {"error": f"upload too large (max {MAX_UPLOAD_BYTES} bytes)"}, 413
        receiver = BoundedZipReceiver()
        try:
            async for chunk in request.stream():
                # receiver.write does a blocking disk write + SHA-256 update; off the event loop like
                # every other blocking call in this file, so a large (up to 1 GiB) upload can't stall
                # other requests on this worker.
                await run_in_threadpool(receiver.write, chunk)
            if receiver.received < length:
                receiver.cleanup()
                return {"error": "upload incomplete (body ended early)"}, 400
            return receiver
        except UploadTooLarge:
            result = {"error": f"upload too large (max {MAX_UPLOAD_BYTES} bytes)"}, 413
        except ClientDisconnect:
            # Starlette raises this from `request.stream()` on an early client disconnect — the ASGI
            # analogue of the stdlib handler's short read, so it gets the same graceful 400.
            result = {"error": "upload interrupted"}, 400
        except OSError:
            # Mirrors the stdlib handler's `_handle_upload`, which returns the same 400 on a
            # write failure (e.g. disk full) instead of letting it surface as a 500.
            result = {"error": "upload interrupted"}, 400
        receiver.cleanup()
        return result

    @app.post("/api/upload")
    async def upload(request: Request) -> JSONResponse:
        """Stream a raw-body zip upload to a temp file (bounded), then bind it as the active config
        (BE-0073) — the FastAPI mirror of the stdlib handler's `_handle_upload`, sharing its bound +
        hash logic via `BoundedZipReceiver` so the two backends can't drift again. Raw body (`?name=`
        for the filename), not multipart: the SPA controls the request, and streaming avoids
        buffering the whole (up to 1 GiB) body in memory."""
        received = await _stream_bounded_body(request)
        if not isinstance(received, BoundedZipReceiver):
            return _result(received)
        filename = request.query_params.get("name") or "bundle.zip"
        try:
            return _result(
                await run_in_threadpool(
                    ops.bind_upload_config,
                    state,
                    received.path,
                    filename,
                    sha256=received.digest(),
                    actor=_actor(request),
                )
            )
        finally:
            received.cleanup()

    async def _artifact_upload(kind: ArtifactKind, request: Request) -> JSONResponse:
        """Stream one independently-uploaded artifact (BE-0268: `config` / `scenarios` / `binary`)
        to a temp file (bounded), then store it (`ops.bind_artifact`) — the FastAPI mirror of the
        stdlib handler's `_handle_artifact_upload`."""
        received = await _stream_bounded_body(request)
        if not isinstance(received, BoundedZipReceiver):
            return _result(received)
        try:
            return _result(
                await run_in_threadpool(
                    ops.bind_artifact,
                    state,
                    kind,
                    received.path,
                    sha256=received.digest(),
                    actor=_actor(request),
                )
            )
        finally:
            received.cleanup()

    @app.post("/api/artifacts/config")
    async def upload_config_artifact(request: Request) -> JSONResponse:
        return await _artifact_upload("config", request)

    @app.post("/api/artifacts/scenarios")
    async def upload_scenarios_artifact(request: Request) -> JSONResponse:
        return await _artifact_upload("scenarios", request)

    @app.post("/api/artifacts/binary")
    async def upload_binary_artifact(request: Request) -> JSONResponse:
        return await _artifact_upload("binary", request)

    @app.get("/api/artifacts/exists")
    async def artifact_exists(request: Request) -> JSONResponse:
        return _result(
            ops.artifact_exists(
                state,
                request.query_params.get("kind"),
                request.query_params.get("sha256"),
                actor=_actor(request),
            )
        )

    @app.post("/api/apikey")
    async def set_api_key(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.set_api_key(state, str(body.get("value", "") or ""), _actor(request)))

    @app.post("/api/claudecodetoken")
    async def set_claude_code_token(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(
            ops.set_claude_code_token(state, str(body.get("value", "") or ""), _actor(request))
        )

    @app.post("/api/gitcredential")
    async def set_git_credential(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(
            ops.set_git_credential(state, str(body.get("value", "") or ""), _actor(request))
        )

    @app.post("/api/provider")
    async def set_provider(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.set_provider(state, body, _actor(request)))

    @app.post("/api/theme")
    async def upload_theme(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.upload_theme(state, body, _actor(request)))

    @app.post("/api/run")
    async def run(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.start_run(state, body, actor=_actor(request)))

    @app.post("/api/projects")
    async def register_project(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.register_project(state, body, actor=_actor(request)))

    @app.post("/api/projects/{name}/run")
    async def run_project(name: str, body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.run_project(state, name, body, actor=_actor(request)))

    @app.post("/api/projects/{name}/activate")
    async def activate_project(name: str, request: Request) -> JSONResponse:
        return _result(ops.activate_project(state, name, actor=_actor(request)))

    @app.delete("/api/projects/{name}")
    async def deregister_project(name: str, request: Request) -> JSONResponse:
        return _result(ops.deregister_project(state, name, actor=_actor(request)))

    @app.post("/api/record")
    async def record(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.start_record(state, body, actor=_actor(request)))

    @app.post("/api/crawl")
    async def crawl(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.start_crawl(state, body, actor=_actor(request)))

    @app.post("/api/triage")
    async def triage(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.start_triage(state, body, actor=_actor(request)))

    @app.post("/api/scenario")
    async def save_scenario(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.save_scenario(state, body, actor=_actor(request)))

    @app.post("/api/scenario/resolve")
    async def resolve_scenario_pick(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.resolve_scenario_pick(state, body, actor=_actor(request)))

    @app.post("/api/lint")
    async def lint_scenario(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.lint_scenario(body))

    @app.post("/api/audit")
    async def audit_scenario(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.audit_scenario(state, body, actor=_actor(request)))

    @app.post("/api/approve")
    async def approve(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.approve_baseline(state, body, actor=_actor(request)))

    @app.post("/api/doctor")
    async def doctor(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.doctor_check(state, body, actor=_actor(request)))

    @app.post("/api/coverage")
    async def coverage(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.coverage_view(state, body, actor=_actor(request)))

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> JSONResponse:
        return _result(ops.cancel_job(state, job_id))

    @app.post("/api/runs/{run_id}/upload-urls")
    async def upload_urls(run_id: str, body: dict[str, Any]) -> JSONResponse:
        return _result(ops.generate_upload_urls(state, run_id, body))

    # Run lifecycle (BE-0239): delete / restore / bulk-delete a run and its report. The static
    # `bulk-delete` path is declared before any `{run_id}` route so it wins the match should a
    # single-segment `POST /api/runs/{run_id}` ever be added (there is none today). Crawl and
    # scenario runs share the `runs/<id>/` tree, so one operation serves both DELETE routes.
    @app.post("/api/runs/bulk-delete")
    async def bulk_delete_runs(body: dict[str, Any], request: Request) -> JSONResponse:
        return _result(ops.bulk_delete_runs(state, body, actor=_actor(request)))

    @app.post("/api/runs/{run_id}/restore")
    async def restore_run(run_id: str, request: Request) -> JSONResponse:
        return _result(ops.restore_run(state, run_id, actor=_actor(request)))

    @app.delete("/api/runs/{run_id}")
    async def delete_run(run_id: str, request: Request, purge: bool = False) -> JSONResponse:
        # `?purge=true` skips the trash window (admin-only, gated in the operation — the query isn't
        # in the path the RBAC middleware sees).
        return _result(ops.delete_run(state, run_id, purge=purge, actor=_actor(request)))

    @app.delete("/api/crawl/runs/{run_id}")
    async def delete_crawl_run(run_id: str, request: Request, purge: bool = False) -> JSONResponse:
        return _result(ops.delete_run(state, run_id, purge=purge, actor=_actor(request)))

    @app.post("/api/worker/lease")
    async def worker_lease(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.worker_lease(state, body.get("worker_id", ""), body.get("capabilities")))

    @app.post("/api/worker/heartbeat")
    async def worker_heartbeat(body: dict[str, Any]) -> JSONResponse:
        return _result(
            ops.worker_heartbeat(state, body.get("worker_id", ""), body.get("job_id", ""))
        )

    @app.post("/api/worker/result")
    async def worker_result(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.worker_result(state, body))

    @app.post("/api/worker/artifact-urls")
    async def worker_artifact_urls(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.worker_artifact_urls(state, body))

    @app.post("/api/worker/scenario-url")
    async def worker_scenario_url(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.worker_scenario_url(state, body))

    return app

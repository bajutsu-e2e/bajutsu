"""The hosted-backend control plane: a FastAPI app over the serve seams (BE-0015 server phase).

`make_app(state)` serves the **same** SPA + API as the local stdlib handler, delegating every
request to the shared `bajutsu.serve.operations` — so the local and hosted backends stay in
lockstep and only differ in which seam implementations the `ServeState` carries. This module is
imported only when the server backend is selected (the import guard in
``tests/serve/test_import_guard.py`` keeps it off the default path); FastAPI lives in the
``server`` optional-dependency group.

The uniform (JSON/text) routes are generated from the shared `bajutsu.serve.routes` registry
(BE-0253 Part 3): `make_app` iterates `ROUTES` and registers each entry that carries a `handle` and
is not `local_only`, so this backend's route table is derived from the same source of truth the
stdlib handler dispatches from — the two can't drift. `off_loop` routes (the SPA index, the
ES-module frontend, run file / range / archive serving, SSE, the OAuth round-trip, login, and the
raw-body uploads) write their own responses and stay bespoke below. `local_only` routes
(`/api/ant/login`, `/api/capture/*`) are deliberately skipped: they need a single-process model
(an in-process `Driver`, a machine-global credential) a horizontally-scaled deployment can't offer.

The transport-specific parts mirror the stdlib handler one-to-one: the auth gate (BE-0051), the
unconditional CSRF Origin check and Host allowlist (BE-0121), the session cookie, and the hardening
response headers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.requests import ClientDisconnect

from bajutsu.serve import gate, oplog
from bajutsu.serve import operations as ops
from bajutsu.serve.handler import (
    _JS_MODULES,
    _OAUTH_STATE_COOKIE,
    _SESSION_COOKIE,
    _asset,
    _index_html,
)
from bajutsu.serve.helpers import range_reply, valid_run_id
from bajutsu.serve.routes import ROUTES, Handle, Route
from bajutsu.serve.state import ServeState
from bajutsu.serve.upload_artifacts import ArtifactKind
from bajutsu.serve.uploads import MAX_UPLOAD_BYTES, BoundedZipReceiver, UploadTooLarge

# How long an idle SSE stream waits before sending a `:keepalive` comment (and rechecking for a
# client disconnect). Short enough to stay under a reverse proxy's idle timeout (BE-0015).
_SSE_KEEPALIVE = 15.0

# Returned by `next(..., _STREAM_DONE)` at exhaustion instead of raising StopIteration (which can't
# cross the threadpool/coroutine boundary cleanly).
_STREAM_DONE: Any = object()

# Route-path constants reused as FastAPI decorators below. The open-endpoint list itself lives in
# `gate.is_open`, shared with the stdlib backend (BE-0253).
_LOGIN_PATH = "/api/login"
_OAUTH_LOGIN = "/api/oauth/login"
_OAUTH_CALLBACK = "/api/oauth/callback"


def _result(payload_status: tuple[Any, int]) -> JSONResponse:
    payload, status = payload_status
    return JSONResponse(payload, status_code=status)


class _FastapiCtx:
    """Adapt one FastAPI request to the backend-neutral `RequestCtx` the route registry expects
    (BE-0253) — the hosted-backend twin of the stdlib handler's `_StdlibCtx`. Starlette has already
    URL-decoded the path params and query, so `path_param`/`query` return them straight through,
    honoring the shared `path_param` "returns the decoded segment" contract without a second
    `unquote`."""

    def __init__(
        self,
        request: Request,
        body: dict[str, Any],
        actor: Callable[[], str | None],
    ) -> None:
        self._request = request
        self._body = body
        self._actor = actor

    def path_param(self, name: str) -> str:
        return str(self._request.path_params[name])

    def query(self, key: str) -> str | None:
        return self._request.query_params.get(key)

    def body(self) -> dict[str, Any]:
        return self._body

    def actor(self) -> str | None:
        return self._actor()


def _serve_artifact(art: Any, request: Request, *, filename: str | None = None) -> Response:
    """Emit an `Artifact` (404 if None): a 302 to its signed URL (server store) or its inline bytes
    (local), honoring a `Range` request (a report's `<video>` needs 206/`Content-Range` to seek).
    `filename`, when given, forces a download via Content-Disposition (the archive route); a redirect
    relies on the signed URL's own disposition. Mirrors the stdlib handler's `_serve_artifact`."""
    if art is None:
        return _result(({"error": "not found"}, 404))
    if art.redirect is not None:  # a server store hands back a signed URL
        return RedirectResponse(art.redirect, status_code=302)
    status, chunk, headers = range_reply(art.body or b"", request.headers.get("range"))
    if status == 416:
        return Response(status_code=416, headers=headers)
    if filename is not None:
        headers = {**headers, "Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(chunk, status_code=status, media_type=art.content_type, headers=headers)


def make_app(state: ServeState) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _authorized(request: Request) -> bool:
        return gate.is_authorized(
            state.auth,
            request.headers.get("authorization", ""),
            request.cookies.get(_SESSION_COOKIE),
            path=request.url.path,
        )

    def _actor(request: Request) -> str | None:
        return gate.actor_for(state.auth, request.cookies.get(_SESSION_COOKIE))

    @app.middleware("http")
    async def _security_gate(request: Request, call_next: Any) -> Response:
        """Host allowlist + auth + CSRF + hardening headers, mirroring the stdlib handler's
        `_gate`/`_host_ok`/`_csrf_ok`/`end_headers` exactly so the two backends enforce the same
        policy (BE-0051/BE-0121)."""
        oplog.bind_request(oplog.new_request_id())
        # DNS-rebinding defense (BE-0121): a Host that names no bound interface is refused ahead of
        # everything else, regardless of the token/CSRF posture. An empty allowlist (a wildcard bind,
        # set by make_asgi_server) accepts any Host, so this is off unless we bound a named interface.
        if not gate.host_allowed(state.allowed_hosts, request.headers.get("host", "")):
            return _hardened(JSONResponse({"error": "host not allowed"}, status_code=403))
        # Block cross-origin state-changing requests unconditionally (BE-0121) — not only when a token
        # is configured. A no-token server would otherwise leave every POST as an unguarded CSRF
        # surface (the CSRF-to-arbitrary-config hole this closes). DELETE (deregister a project,
        # BE-0225) is equally state-changing, so it is guarded too. No Origin (a non-browser client)
        # passes, matching the stdlib `_csrf_ok`.
        if request.method in ("POST", "DELETE") and not gate.csrf_ok(
            request.headers.get("origin"), request.headers.get("host") or ""
        ):
            return _hardened(
                JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
            )
        if state.auth.token is not None:
            path, method = request.url.path, request.method
            if not gate.is_open(method, path) and not _authorized(request):
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
        # The shared hardening headers on every response (BE-0051); defined once in `gate` so both
        # backends emit the identical set (BE-0253).
        response.headers.update(gate.HARDENING_HEADERS)
        return response

    # --- SPA + static run artifacts (off_loop, bespoke) ---

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        # Lockstep with the stdlib handler: forward the drop-in themes dir + default (BE-0191).
        return HTMLResponse(_index_html(state.themes_dir, state.default_theme))

    @app.get("/serve.{name}.mjs")
    async def frontend_module(name: str) -> Response:
        # The ES-module frontend (BE-0247): the index loads these via <script type="module">, so the
        # hosted backend serves them alongside the stdlib handler (lockstep). Resolve the request to
        # the matching bundled module *constant* (not a string built from `name`), so an unknown or
        # traversing name 404s and no user-derived value ever reaches the file read (path-injection).
        # The `text/javascript` type is required for a browser to execute a module script. Not in the
        # route registry: its intra-segment `{name}` template (serve.{name}.mjs) is a shape the shared
        # matcher does not model, so both backends serve it bespoke.
        asset = next((m for m in _JS_MODULES if m == f"serve.{name}.mjs"), None)
        if asset is None:
            return _result(({"error": "not found"}, 404))
        return Response(_asset(asset), media_type="text/javascript; charset=utf-8")

    @app.get("/runs/{run_id}/archive.zip")
    async def run_archive(run_id: str, request: Request) -> Response:
        # A one-file download of the whole run (BE-0060), through the same org-scoped store as the
        # per-file route, so containment / multi-tenancy hold identically. Reject a non-segment id
        # up front so no `/` reaches the filename header (HTTP-splitting) — mirrors the stdlib
        # handler's `_serve_run_archive`. Declared before `/runs/{rel:path}` so the greedy run-file
        # route doesn't swallow it.
        if not valid_run_id(run_id):
            return _result(({"error": "not found"}, 404))
        store = state.for_org(state.org_of(_actor(request))).artifacts
        return _serve_artifact(store.archive(run_id), request, filename=f"{run_id}.zip")

    @app.get("/runs/{rel:path}")
    async def run_file(rel: str, request: Request) -> Response:
        # The actor's org-scoped artifact store: a run in another org's prefix reads as not-found
        # (BE-0015 multi-tenancy). report.html renders on view from the stored model (BE-0068).
        store = state.for_org(state.org_of(_actor(request))).artifacts
        return _serve_artifact(ops.run_file(store, rel), request)

    # --- SSE (off_loop) ---

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

    # --- login + OAuth round-trip (off_loop, set cookies) ---

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

    # --- raw-body uploads (off_loop) ---

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

    # --- uniform (JSON / text) routes, generated from the shared registry (BE-0253 Part 3) ---

    def _register(route: Route) -> None:
        """Register one uniform route as a FastAPI endpoint that dispatches through the registry's
        shared `handle` adapter — the FastAPI half of the single source of truth both backends
        consume, so the route table can no longer drift between them."""
        handle: Handle | None = route.handle
        if handle is None:  # only uniform routes reach here; narrows the type for the closure
            return

        async def endpoint(request: Request) -> Response:
            body: dict[str, Any] = {}
            if route.method == "POST":
                # Parse the body the same way the stdlib handler does (empty -> {}, malformed -> 400,
                # non-object -> 400) rather than via a typed `dict` param, so a bodyless POST (cancel,
                # activate, restore) isn't forced to 422 and the two backends reject malformed input
                # identically.
                try:
                    parsed = json.loads(await request.body() or b"{}")
                except json.JSONDecodeError:
                    return _result(({"error": "bad json"}, 400))
                if not isinstance(parsed, dict):
                    return _result(({"error": "expected a JSON object"}, 400))
                body = parsed
            ctx = _FastapiCtx(request, body, lambda: _actor(request))
            # The `ops` call blocks (disk / network / subprocess), so run it off the event loop —
            # uniformly, so a route like the from-Git config bind or compose stays non-blocking
            # exactly as its hand-written predecessor did.
            payload, code = await run_in_threadpool(handle, state, ctx)
            if route.content_type is not None:
                return Response(payload, status_code=code, media_type=route.content_type)
            return _result((payload, code))

        app.add_api_route(route.path, endpoint, methods=[route.method])

    for route in ROUTES:
        if route.handle is not None and not route.local_only:
            _register(route)

    return app

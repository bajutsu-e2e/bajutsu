"""The hosted-backend control plane: a FastAPI app over the serve seams (BE-0015 server phase).

`make_app(state)` serves the **same** SPA + API as the local stdlib handler, delegating every
request to the shared `bajutsu.serve.operations` — so the local and hosted backends stay in
lockstep and only differ in which seam implementations the `ServeState` carries. This module is
imported only when the server backend is selected (the import guard in
``tests/serve/test_import_guard.py`` keeps it off the default path); FastAPI lives in the
``server`` optional-dependency group.

The transport-specific parts mirror the stdlib handler one-to-one: the auth gate (BE-0051), the
CSRF Origin check, the session cookie, and the hardening response headers. Live-log SSE streaming
arrives with a later slice; the rest of the API surface is here.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from bajutsu.serve import operations as ops
from bajutsu.serve.handler import _SESSION_COOKIE, _index_html
from bajutsu.serve.jobs import ServeState

# Endpoints reachable without a credential when a token is configured (mirrors the stdlib gate):
# the index page so the login UI can load, and the login endpoint itself.
_OPEN_GET = ("/", "/index.html")
_LOGIN_PATH = "/api/login"


def _result(payload_status: tuple[Any, int]) -> JSONResponse:
    payload, status = payload_status
    return JSONResponse(payload, status_code=status)


def make_app(state: ServeState) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _authorized(request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and state.check_token(auth[len("Bearer ") :]):
            return True
        sid = request.cookies.get(_SESSION_COOKIE)
        return sid is not None and state.valid_session(sid)

    @app.middleware("http")
    async def gate(request: Request, call_next: Any) -> Response:
        """Auth + CSRF + hardening headers, mirroring the stdlib handler's `_gate`/`_csrf_ok`/
        `end_headers` exactly so the two backends enforce the same policy."""
        if state.token is not None:
            path, method = request.url.path, request.method
            open_path = (method == "GET" and path in _OPEN_GET) or (
                method == "POST" and path == _LOGIN_PATH
            )
            if not open_path and not _authorized(request):
                return _hardened(JSONResponse({"error": "unauthorized"}, status_code=401))
            # Block cross-origin state-changing requests when auth (the cookie) is in play.
            if method == "POST":
                origin = request.headers.get("origin")
                if origin and urlparse(origin).netloc != (request.headers.get("host") or ""):
                    return _hardened(
                        JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
                    )
        return _hardened(await call_next(request))

    def _hardened(response: Response) -> Response:
        # Standard hardening headers on every response (BE-0051): block MIME sniffing and framing
        # (clickjacking), and don't leak the URL via Referer.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # --- SPA + static run artifacts ---

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(_index_html())

    @app.get("/runs/{rel:path}")
    async def run_file(rel: str) -> Response:
        art = state.artifacts.get(rel)
        if art is None:
            return _result(({"error": "not found"}, 404))
        if art.redirect is not None:  # a server store hands back a signed URL
            return RedirectResponse(art.redirect, status_code=302)
        return Response(art.body or b"", media_type=art.content_type)

    # --- GET (reads) ---

    @app.get("/api/scenarios")
    async def scenarios(app: str | None = None) -> JSONResponse:
        return _result(ops.list_scenarios(state, app))

    @app.get("/api/apps")
    async def apps() -> JSONResponse:
        return _result(ops.list_apps_payload(state))

    @app.get("/api/config")
    async def config() -> JSONResponse:
        return _result(ops.config_info(state))

    @app.get("/api/fs")
    async def fs(dir: str | None = None) -> JSONResponse:
        return _result(ops.browse_fs(state, dir))

    @app.get("/api/apikey")
    async def api_key(reveal: str | None = None) -> JSONResponse:
        return _result(ops.api_key_info(state, bool(reveal)))

    @app.get("/api/provider")
    async def get_provider() -> JSONResponse:
        return _result(ops.provider_info(state))

    @app.get("/api/simulators")
    async def simulators() -> JSONResponse:
        return _result(ops.simulators_payload(state))

    @app.get("/api/runs")
    async def runs() -> JSONResponse:
        return _result(ops.runs_payload(state))

    @app.get("/api/scenario")
    async def read_scenario(app: str | None = None, path: str | None = None) -> JSONResponse:
        return _result(ops.read_scenario(state, app, path))

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str) -> JSONResponse:
        return _result(ops.job_view(state, job_id))

    # --- POST (actions) ---

    @app.post(_LOGIN_PATH)
    async def login(body: dict[str, Any]) -> JSONResponse:
        payload, status, sid = ops.login(state, str(body.get("token", "") or ""))
        resp = JSONResponse(payload, status_code=status)
        if sid is not None:
            resp.set_cookie(_SESSION_COOKIE, sid, httponly=True, samesite="strict", path="/")
        return resp

    @app.post("/api/config")
    async def bind_config(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.bind_config(state, str(body.get("path", "") or "")))

    @app.post("/api/apikey")
    async def set_api_key(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.set_api_key(state, str(body.get("value", "") or "")))

    @app.post("/api/provider")
    async def set_provider(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.set_provider(state, body))

    @app.post("/api/run")
    async def run(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.start_run(state, body))

    @app.post("/api/record")
    async def record(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.start_record(state, body))

    @app.post("/api/crawl")
    async def crawl(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.start_crawl(state, body))

    @app.post("/api/scenario")
    async def save_scenario(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.save_scenario(state, body))

    @app.post("/api/approve")
    async def approve(body: dict[str, Any]) -> JSONResponse:
        return _result(ops.approve_baseline(state, body))

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> JSONResponse:
        return _result(ops.cancel_job(state, job_id))

    return app

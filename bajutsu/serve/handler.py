"""HTTP request handler for ``bajutsu serve`` (the local stdlib backend).

A thin transport over `bajutsu.serve.operations`: this module owns only the stdlib-specific parts
— auth / CSRF / cookies / security headers, JSON encoding, SSE streaming, and serving the SPA and
run artifacts. The request-handling logic itself lives in `operations`, shared with the hosted
FastAPI control plane so the two backends stay in lockstep (BE-0015).
"""

from __future__ import annotations

import functools
import json
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from jinja2 import Environment, FileSystemLoader

from bajutsu.serve import operations as ops
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.jobs import ServeState

# Session cookie set at login when a serve token is configured (BE-0051).
_SESSION_COOKIE = "bajutsu_session"
_OAUTH_STATE_COOKIE = (
    "bajutsu_oauth_state"  # short-lived CSRF state for the OAuth round-trip (7b-2)
)


def _make_handler(state: ServeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def end_headers(self) -> None:
            # Standard hardening headers on every response (BE-0051): block MIME sniffing and
            # cross-origin framing (clickjacking), and don't leak the URL via Referer. SAMEORIGIN
            # (not DENY) so the Replay view can frame its own run report (/runs/<id>/report.html);
            # cross-origin framing stays blocked.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def _json(self, payload: Any, code: int = 200, cookie: str | None = None) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _sse_job(self, job_id: str) -> None:
            """Stream a job's log over SSE via the shared event stream: a `log` event per line
            (backlog + live from the LogBus), then a terminal `done` event carrying the job's final
            view. The buffered bus means a subscriber that attaches after the job finished still
            replays everything."""
            events = ops.job_log_events(state, job_id)
            if events is None:
                self._json({"error": "no such job"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")  # don't let a proxy buffer the stream
            self.end_headers()
            try:
                for event, data in events:
                    self.wfile.write(ops.format_sse(event, data).encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client navigated away; stop streaming

        def _authorized(self) -> bool:
            """A request is authorized by a valid `Authorization: Bearer <token>` header (API
            clients) or a valid session cookie (the browser, after POST /api/login)."""
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer ") and state.check_token(auth[len("Bearer ") :]):
                return True
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(_SESSION_COOKIE)
            return morsel is not None and state.valid_session(morsel.value)

        def _gate(self) -> bool:
            """Authentication gate (BE-0051). With no token configured the server is open
            (loopback-only legacy behavior). Otherwise every request must be authorized, except
            the index page (so the login UI can load) and the login endpoint itself. Sends 401
            and returns False when a required credential is missing."""
            if state.token is None:
                return True
            path = urlparse(self.path).path
            if self.command == "GET" and path in (
                "/",
                "/index.html",
                "/api/oauth/login",
                "/api/oauth/callback",
            ):
                return True
            if self.command == "POST" and path == "/api/login":
                return True
            if self._authorized():
                # Authenticated. For an OAuth session (an identity) with a database wired, enforce
                # the user's role on mutating endpoints (BE-0015 7c-2). A token/Bearer request has
                # no identity and stays full-access (the operator credential).
                login = self._actor()
                if (
                    login is not None
                    and state.repository is not None
                    and ops.forbidden_for_role(state, login, self.command, path)
                ):
                    length = int(self.headers.get("Content-Length") or 0)
                    if length:
                        self.rfile.read(length)
                    self._json({"error": "forbidden"}, 403)
                    return False
                return True
            # Drain any request body before replying, so a keep-alive connection isn't left with
            # unread bytes that would corrupt the next request on it.
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._json({"error": "unauthorized"}, 401)
            return False

        def _qs(self, key: str) -> str | None:
            return next(iter(parse_qs(urlparse(self.path).query).get(key) or []), None)

        def _actor(self) -> str | None:
            """The GitHub login bound to this request's session, if any — used to attribute audit
            entries (BE-0015 7c). None for a token/Bearer request or no session."""
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(_SESSION_COOKIE)
            return state.sessions.identity(morsel.value) if morsel is not None else None

        def do_GET(self) -> None:
            if not self._gate():
                return
            path = urlparse(self.path).path
            match path:
                case "/" | "/index.html":
                    body = _index_html().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                case "/api/scenarios":
                    self._json(*ops.list_scenarios(state, self._qs("target"), actor=self._actor()))
                case "/api/targets":
                    self._json(*ops.list_targets_payload(state, actor=self._actor()))
                case "/api/config":
                    self._json(*ops.config_info(state))
                case "/api/fs":
                    self._json(*ops.browse_fs(state, self._qs("dir")))
                case "/api/apikey":
                    self._json(*ops.api_key_info(state, bool(self._qs("reveal"))))
                case "/api/provider":
                    self._json(*ops.provider_info(state))
                case "/api/simulators":
                    self._json(*ops.simulators_payload(state))
                case "/api/runs":
                    self._json(*ops.runs_payload(state, actor=self._actor()))
                case "/api/scenario":
                    self._json(
                        *ops.read_scenario(
                            state, self._qs("target"), self._qs("path"), actor=self._actor()
                        )
                    )
                case "/api/oauth/login":
                    self._oauth_login()
                case "/api/oauth/callback":
                    self._oauth_callback()
                case _ if path.startswith("/api/jobs/") and path.endswith("/events"):
                    self._sse_job(path[len("/api/jobs/") : -len("/events")])
                case _ if path.startswith("/api/jobs/"):
                    self._json(*ops.job_view(state, path[len("/api/jobs/") :]))
                case _ if path.startswith("/runs/") and path.endswith("/archive.zip"):
                    self._serve_run_archive(unquote(path[len("/runs/") : -len("/archive.zip")]))
                case _ if path.startswith("/runs/"):
                    self._serve_run_file(unquote(path[len("/runs/") :]))
                case _:
                    self._json({"error": "not found"}, 404)

        def _csrf_ok(self) -> bool:
            """CSRF defense (BE-0051), defense-in-depth atop the SameSite session cookie: if an
            `Origin` header is present it must match the `Host`. Non-browser clients (no Origin,
            no ambient cookie) are allowed; a cross-origin browser request is blocked."""
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlparse(origin).netloc == (self.headers.get("Host") or "")

        def do_POST(self) -> None:
            if not self._gate():
                return
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            # Block cross-origin state-changing requests when auth (the cookie) is in play.
            if state.token is not None and not self._csrf_ok():
                if length:
                    self.rfile.read(length)  # drain so keep-alive isn't left with unread bytes
                self._json({"error": "cross-origin request blocked"}, 403)
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            if not isinstance(body, dict):
                # Handlers treat the body as a mapping; reject a non-object JSON (a list, string,
                # number) here rather than 500 on a `.get(...)`.
                self._json({"error": "expected a JSON object"}, 400)
                return
            match path:
                case "/api/login":
                    self._post_login(body)
                case "/api/config":
                    # `git` selects the from-Git picker (BE-0063); `path` the local file browser.
                    if body.get("git"):
                        self._json(*ops.bind_git_config(state, str(body.get("git") or "")))
                    else:
                        self._json(*ops.bind_config(state, str(body.get("path", "") or "")))
                case "/api/apikey":
                    self._json(*ops.set_api_key(state, str(body.get("value", "") or "")))
                case "/api/provider":
                    self._json(*ops.set_provider(state, body))
                case "/api/run":
                    self._json(*ops.start_run(state, body, actor=self._actor()))
                case "/api/record":
                    self._json(*ops.start_record(state, body, actor=self._actor()))
                case "/api/crawl":
                    self._json(*ops.start_crawl(state, body, actor=self._actor()))
                case "/api/scenario":
                    self._json(*ops.save_scenario(state, body, actor=self._actor()))
                case "/api/approve":
                    self._json(*ops.approve_baseline(state, body, actor=self._actor()))
                case _ if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                    self._json(*ops.cancel_job(state, path[len("/api/jobs/") : -len("/cancel")]))
                case _:
                    self._json({"error": "not found"}, 404)

        def _post_login(self, body: dict[str, Any]) -> None:
            """Exchange the shared token for a session cookie (BE-0051). The token is sent in the
            POST body (never a URL); on success the response sets an HttpOnly, SameSite cookie
            holding an opaque session id — so the token itself is never stored in the browser."""
            payload, status, sid = ops.login(state, str(body.get("token", "") or ""))
            cookie = (
                f"{_SESSION_COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/"
                if sid is not None
                else None
            )
            self._json(payload, status, cookie=cookie)

        def _oauth_login(self) -> None:
            """Begin GitHub OAuth (BE-0015 7b-2): redirect to GitHub's authorize URL and stash the
            CSRF state in a short-lived cookie (SameSite=Lax so it survives the redirect back)."""
            payload, status, csrf = ops.oauth_login(state)
            if status != 200 or csrf is None:
                self._json(payload, status)
                return
            self.send_response(302)
            self.send_header("Location", payload["redirect"])
            self.send_header(
                "Set-Cookie",
                f"{_OAUTH_STATE_COOKIE}={csrf}; HttpOnly; SameSite=Lax; Path=/; Max-Age=600",
            )
            self.end_headers()

        def _oauth_callback(self) -> None:
            """Complete GitHub OAuth: compare the returned state to the cookie, exchange the code,
            and on success set the session cookie, clear the state cookie, and land on the app."""
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(_OAUTH_STATE_COOKIE)
            payload, status, sid = ops.oauth_callback(
                state,
                self._qs("code") or "",
                self._qs("state") or "",
                morsel.value if morsel is not None else "",
            )
            if status != 200 or sid is None:
                self._json(payload, status)
                return
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie", f"{_SESSION_COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/"
            )
            self.send_header(
                "Set-Cookie", f"{_OAUTH_STATE_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
            )
            self.end_headers()

        def _artifacts(self) -> Any:
            # The actor's org-scoped artifact store: a run in another org's prefix reads as
            # not-found (BE-0015 multi-tenancy).
            return state.for_org(state.org_of(self._actor())).artifacts

        def _serve_artifact(self, art: Any, *, filename: str | None = None) -> None:
            """Emit an `Artifact` (404 if None): a 302 to its signed URL (server store) or its
            inline bytes (local). For the inline case, `filename` (when given) forces a download via
            Content-Disposition; a redirect relies on the signed URL's own disposition."""
            if art is None:
                self._json({"error": "not found"}, 404)
                return
            if art.redirect is not None:  # a server store hands back a signed URL
                self.send_response(302)
                self.send_header("Location", art.redirect)
                self.end_headers()
                return
            data = art.body or b""
            self.send_response(200)
            self.send_header("Content-Type", art.content_type)
            if filename is not None:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_run_file(self, rel: str) -> None:
            self._serve_artifact(self._artifacts().get(rel))

        def _serve_run_archive(self, run_id: str) -> None:
            # A one-file download of the whole run (BE-0060), through the same org-scoped store, so
            # containment / multi-tenancy hold identically to the per-file route. Reject a non-segment
            # id (e.g. `<id>/demo`) up front, so no `/` reaches the filename header (HTTP-splitting).
            if not valid_run_id(run_id):
                self._json({"error": "not found"}, 404)
                return
            self._serve_artifact(self._artifacts().archive(run_id), filename=f"{run_id}.zip")

        def log_message(self, *_args: Any) -> None:  # silence per-request stderr logging
            pass

    return Handler


def make_server(state: ServeState, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _make_handler(state))


# The SPA shell + its CSS/JS/themes live in bajutsu/templates/serve.* — split out of this
# module so they read/edit as real files. We inline them into one self-contained response,
# mirroring report.py (no separate /static routes; this is a localhost dev tool).
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


@functools.lru_cache(maxsize=4)
def _asset(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _index_html() -> str:
    return (
        _env()
        .get_template("serve.html.j2")
        .render(
            css=_asset("serve.css"),
            themes_css=_asset("serve.themes.css"),
            js=_asset("serve.js"),
        )
    )

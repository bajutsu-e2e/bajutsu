"""HTTP request handler for ``bajutsu serve`` (the local stdlib backend).

A thin transport over `bajutsu.serve.operations`: this module owns only the stdlib-specific parts
— auth / CSRF / cookies / security headers, JSON encoding, SSE streaming, and serving the SPA and
run artifacts. The request-handling logic itself lives in `operations`, shared with the hosted
FastAPI control plane so the two backends stay in lockstep (BE-0015).
"""

from __future__ import annotations

import functools
import hashlib
import json
import tempfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from jinja2 import Environment, FileSystemLoader

from bajutsu.serve import operations as ops
from bajutsu.serve import oplog
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.uploads import MAX_UPLOAD_BYTES

# Stream an uploaded bundle to disk in 1 MiB chunks so a large app binary never loads into memory.
_UPLOAD_CHUNK = 1024 * 1024

# Host-header values that always name this machine (BE-0121 DNS-rebinding defense).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _allowed_hosts(host: str) -> frozenset[str]:
    """The `Host`-header hostnames a server bound to *host* accepts (BE-0121).

    A loopback bind accepts every loopback name; a specific host adds that name (still reachable via
    loopback locally). A wildcard bind (``0.0.0.0`` / ``::`` / empty) can't enumerate its reachable
    names — the operator chose broad exposure, often behind a proxy with its own hostname — so it
    returns an empty set, which disables Host enforcement (CSRF stays the cross-origin guard).
    """
    if host in ("", "0.0.0.0", "::"):  # noqa: S104 — matching a wildcard bind, not binding one
        return frozenset()
    normalized = host.lower()
    if normalized in _LOOPBACK_HOSTS:
        return _LOOPBACK_HOSTS
    return _LOOPBACK_HOSTS | {normalized}


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
            if not self._host_ok():
                # DNS-rebinding defense (BE-0121): a Host that names no bound interface is refused
                # ahead of everything else, so a rebound hostname reaches no endpoint at all
                # (including GET /api/apikey), regardless of the token/CSRF posture. Close
                # the connection rather than draining the body: a rejected request needs no
                # keep-alive, and this is the one gate an unbounded /api/upload body can hit, so
                # draining Content-Length here would read the whole upload just to discard it.
                self.close_connection = True
                self._json({"error": "host not allowed"}, 403)
                return False
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
            oplog.bind_request(oplog.new_request_id())
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
                    self._json(*ops.api_key_info(state, self._actor()))
                case "/api/provider":
                    self._json(*ops.provider_info(state))
                case "/api/simulators":
                    self._json(*ops.simulators_payload(state))
                case "/api/runs":
                    self._json(*ops.runs_payload(state, actor=self._actor()))
                case "/stats":
                    html, code = ops.stats_html(state, actor=self._actor())
                    body = html.encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                case "/api/scenario":
                    self._json(
                        *ops.read_scenario(
                            state,
                            self._qs("target"),
                            self._qs("path"),
                            actor=self._actor(),
                            run_id=self._qs("runId"),
                            scenario_name=self._qs("scenario"),
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
                case "/api/capture/screenshot":
                    self._serve_capture_screenshot()
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

        def _host_ok(self) -> bool:
            """DNS-rebinding defense (BE-0121): the request's `Host` must name an interface serve is
            bound to. An empty allowlist (a wildcard bind, whose reachable names can't be enumerated)
            accepts any Host; a loopback/named bind enforces its own names, so a page that rebinds a
            hostname to 127.0.0.1 can't reach the loopback server through a same-origin request."""
            if not state.allowed_hosts:
                return True
            host = urlparse(f"//{self.headers.get('Host', '')}").hostname
            return host in state.allowed_hosts

        def do_POST(self) -> None:
            oplog.bind_request(oplog.new_request_id())
            if not self._gate():
                return
            path = urlparse(self.path).path
            # A bundle upload (BE-0073) carries a raw zip body, not JSON, and can be large — handle
            # it before the JSON read so it streams to disk instead of loading into memory.
            if path == "/api/upload":
                self._handle_upload()
                return
            length = int(self.headers.get("Content-Length") or 0)
            # Block cross-origin state-changing requests unconditionally (BE-0121) — not only when a
            # token is configured. The no-token loopback default is the common `make serve` case, and
            # an unguarded POST there is the CSRF-to-arbitrary-config hole this closes.
            if not self._csrf_ok():
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
                    # A `git` key selects the from-Git picker (BE-0063); `path` the local file
                    # browser. Key presence (not truthiness) routes, so an empty `git` still reaches
                    # the Git binder and gets its "spec is required" 400, not the local one.
                    if "git" in body:
                        self._json(*ops.bind_git_config(state, str(body.get("git") or "")))
                    else:
                        self._json(*ops.bind_config(state, str(body.get("path", "") or "")))
                case "/api/apikey":
                    self._json(
                        *ops.set_api_key(state, str(body.get("value", "") or ""), self._actor())
                    )
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
                case "/api/scenario/resolve":
                    self._json(*ops.resolve_scenario_pick(state, body, actor=self._actor()))
                case "/api/enrich":
                    self._json(*ops.start_enrich(state, body, actor=self._actor()))
                case "/api/doctor":
                    self._json(*ops.doctor_check(state, body, actor=self._actor()))
                case "/api/capture/start":
                    self._json(*ops.start_capture(state, body, actor=self._actor()))
                case "/api/capture/mark":
                    self._json(*ops.mark_capture(state, body, actor=self._actor()))
                case "/api/capture/finish":
                    self._json(*ops.finish_capture(state, body, actor=self._actor()))
                case "/api/worker/lease":
                    self._json(*ops.worker_lease(state, body.get("worker_id", "")))
                case "/api/worker/heartbeat":
                    self._json(
                        *ops.worker_heartbeat(
                            state, body.get("worker_id", ""), body.get("job_id", "")
                        )
                    )
                case "/api/worker/result":
                    self._json(*ops.worker_result(state, body))
                case _ if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                    self._json(*ops.cancel_job(state, path[len("/api/jobs/") : -len("/cancel")]))
                case _ if path.startswith("/api/runs/") and path.endswith("/upload-urls"):
                    run_id = path[len("/api/runs/") : -len("/upload-urls")]
                    self._json(*ops.generate_upload_urls(state, run_id, body))
                case _:
                    self._json({"error": "not found"}, 404)

        def _handle_upload(self) -> None:
            """Stream a raw-body zip upload to a temp file (bounded), then bind it as the active config
            (BE-0073). Raw body (`Content-Type: application/zip`, filename via `?name=`), not
            multipart: the SPA controls the request, and a streamed body needs no parser — the first
            POST here that doesn't read a JSON body. The size cap is enforced both up front
            (Content-Length) and while reading, so a lying length can't overrun it."""
            # These early returns don't read the (possibly huge) body, so the connection still holds
            # the unread upload bytes. Close it rather than draining gigabytes or leaving them to
            # corrupt the next request on a keep-alive connection.
            if not self._csrf_ok():  # unconditional cross-origin block (BE-0121)
                self.close_connection = True
                self._json({"error": "cross-origin request blocked"}, 403)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._json({"error": "empty upload"}, 400)
                return
            if length > MAX_UPLOAD_BYTES:
                self.close_connection = True
                self._json({"error": f"upload too large (max {MAX_UPLOAD_BYTES} bytes)"}, 413)
                return
            filename = self._qs("name") or "bundle.zip"
            tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115
            tmp_path = Path(tmp.name)
            digest = (
                hashlib.sha256()
            )  # hash while streaming so the file is read once, not again to hash
            try:
                remaining = length
                with tmp:
                    while remaining > 0:
                        chunk = self.rfile.read(min(_UPLOAD_CHUNK, remaining))
                        if not chunk:
                            break  # client closed early; `remaining > 0` below catches the short read
                        remaining -= len(chunk)
                        digest.update(chunk)
                        tmp.write(chunk)
                if remaining > 0:
                    # Body ended before Content-Length: a truncated upload. Fail explicitly (don't
                    # hand a partial zip downstream as an "invalid bundle"), and close the connection
                    # since its framing is now unreliable.
                    self.close_connection = True
                    self._json({"error": "upload incomplete (body ended early)"}, 400)
                    return
                self._json(
                    *ops.bind_upload_config(
                        state, tmp_path, filename, sha256=digest.hexdigest(), actor=self._actor()
                    )
                )
            except OSError:
                self.close_connection = True
                self._json({"error": "upload interrupted"}, 400)
            finally:
                tmp_path.unlink(missing_ok=True)

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
            # report.html is rendered on view from the stored model (BE-0068); other files served as-is.
            self._serve_artifact(ops.run_file(self._artifacts(), rel))

        def _serve_capture_screenshot(self) -> None:
            session = state.capture
            if session is None or not session.screenshot_path.exists():
                self._json({"error": "no active capture session"}, 404)
                return
            if session.actor is not None and self._actor() != session.actor:
                self._json({"error": "capture session belongs to another user"}, 403)
                return
            data = session.screenshot_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

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
    # Derive the Host allowlist from the interface we actually bind, so `_gate` can reject a
    # rebound hostname (BE-0121). A wildcard bind yields an empty allowlist (enforcement off).
    state.allowed_hosts = _allowed_hosts(host)
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

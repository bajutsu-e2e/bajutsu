"""HTTP request handler for ``bajutsu serve`` (the local stdlib backend).

A thin transport over `bajutsu.serve.operations`: this module owns only the stdlib-specific parts
— auth / CSRF / cookies / security headers, JSON encoding, SSE streaming, and serving the SPA and
run artifacts. The request-handling logic itself lives in `operations`, shared with the hosted
FastAPI control plane so the two backends stay in lockstep (BE-0015).
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from jinja2 import Environment, FileSystemLoader

from bajutsu.serve import gate, oplog
from bajutsu.serve import operations as ops
from bajutsu.serve._paths import TEMPLATES_DIR as _TEMPLATE_DIR
from bajutsu.serve.helpers import range_reply, valid_run_id
from bajutsu.serve.routes import ROUTES, match_route
from bajutsu.serve.state import ServeState
from bajutsu.serve.upload_artifacts import ArtifactKind
from bajutsu.serve.uploads import MAX_UPLOAD_BYTES, BoundedZipReceiver, UploadTooLarge

# Stream an uploaded bundle to disk in 1 MiB chunks so a large app binary never loads into memory.
_UPLOAD_CHUNK = 1024 * 1024

# The three independently-uploadable artifact routes (BE-0268), each a raw-body POST mirroring
# `/api/upload`'s own streaming shape — mapping path to kind lets `do_POST` dispatch with one lookup
# instead of a per-kind `if`.
_ARTIFACT_UPLOAD_PATHS: dict[str, ArtifactKind] = {
    "/api/artifacts/config": "config",
    "/api/artifacts/scenarios": "scenarios",
    "/api/artifacts/binary": "binary",
}

# Session cookie set at login when a serve token is configured (BE-0051).
_SESSION_COOKIE = "bajutsu_session"
_OAUTH_STATE_COOKIE = (
    "bajutsu_oauth_state"  # short-lived CSRF state for the OAuth round-trip (7b-2)
)


class _StdlibCtx:
    """Adapt one stdlib request to the backend-neutral `RequestCtx` the route registry expects
    (BE-0253): path parameters from the matcher, the query and session actor via the handler's
    own `_qs`/`_actor`, and the already-parsed JSON body ({} for a GET)."""

    def __init__(
        self,
        params: dict[str, str],
        body: dict[str, Any],
        qs: Callable[[str], str | None],
        actor: Callable[[], str | None],
    ) -> None:
        self._params = params
        self._body = body
        self._qs = qs
        self._actor = actor

    def path_param(self, name: str) -> str:
        # The matcher runs on the raw (still percent-encoded) request path, so decode here to honor
        # the `RequestCtx.path_param` contract of returning the decoded segment — the FastAPI ctx's
        # Starlette params arrive already decoded, so both backends hand closures the same value.
        return unquote(self._params[name])

    def query(self, key: str) -> str | None:
        return self._qs(key)

    def body(self) -> dict[str, Any]:
        return self._body

    def actor(self) -> str | None:
        return self._actor()


def _make_handler(state: ServeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def end_headers(self) -> None:
            # The shared hardening headers on every response (BE-0051); defined once in `gate` so
            # both backends emit the identical set (BE-0253).
            for name, value in gate.HARDENING_HEADERS.items():
                self.send_header(name, value)
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

        def _text(self, text: str, code: int, content_type: str) -> None:
            """Write a text body with an explicit content type — the shared shape behind the
            non-JSON GET routes (/stats HTML, /metrics Prometheus)."""
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _respond_uncaught(self, exc: Exception) -> None:
            """Turn an uncaught dispatch exception into a JSON 500 (BE-0264).

            Without this, an operation that *raises* (rather than returning an error tuple)
            propagates to ``socketserver``, which drops the connection with no body — leaving the
            browser at ``Unexpected end of JSON input``. The full traceback is always logged (with
            the request id bound via ``oplog``) so the operator keeps the diagnostic; the client
            gets the exception's *message* — deliberately (design Unit 3): the readable operation
            error is the whole point (e.g. "xcuitest backend requires a runner_port"), so we echo
            it rather than a generic string, at the cost of a message that may name an internal
            path. The traceback itself is never sent.

            Writing the 500 can itself fail if the client already went away (a `wfile` write on a
            dead socket): the wrapped dispatch under the boundary hits the operation *before* any
            bytes are written, so a normal raise reaches here pre-response — but a disconnect
            mid-write would. In that case we've already logged the real error, so we only close the
            connection rather than let the write error re-propagate to ``socketserver`` (which is
            exactly the empty-body drop this boundary exists to prevent).
            """
            logging.getLogger(__name__).exception(
                "unhandled error dispatching %s %s", self.command, self.path
            )
            try:
                self._json({"error": str(exc) or exc.__class__.__name__}, 500)
            except Exception:
                self.close_connection = True

        def _serve_module(self, name: str) -> None:
            """Serve one serve.*.mjs frontend module (BE-0247). The caller already validated `name`
            is a _MODULE_PATHS member (its leading `/` stripped); resolve it to the matching
            _JS_MODULES *constant* so no user-derived value reaches the file read (path-injection),
            then serve it. `text/javascript` is required: a browser refuses to execute a module
            script served under any other MIME type."""
            asset = next((m for m in _JS_MODULES if m == name), None)
            if asset is None:  # unreachable via the gated route; defensive against a future caller
                self._json({"error": "not found"}, 404)
                return
            self._text(_asset(asset), 200, "text/javascript; charset=utf-8")

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

        def _session_value(self) -> str | None:
            """This request's `bajutsu_session` cookie value, if any — the stdlib-side read behind
            the shared `gate.is_authorized` / `gate.actor_for` policy (BE-0253)."""
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(_SESSION_COOKIE)
            return morsel.value if morsel is not None else None

        def _authorized(self) -> bool:
            return gate.is_authorized(
                state.auth,
                self.headers.get("Authorization", ""),
                self._session_value(),
                path=urlparse(self.path).path,
            )

        def _gate(self) -> bool:
            """Authentication gate (BE-0051). With no token configured the server is open
            (loopback-only legacy behavior). Otherwise every request must be authorized, except
            the index page and the frontend ES-module routes (so the login UI and its JS can load,
            BE-0247) and the login endpoint itself. Sends 401 and returns False when a required
            credential is missing."""
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
            if state.auth.token is None:
                return True
            path = urlparse(self.path).path
            if gate.is_open(self.command, path):
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
            return gate.actor_for(state.auth, self._session_value())

        def do_GET(self) -> None:
            oplog.bind_request(oplog.new_request_id())
            if not self._gate():
                return
            path = urlparse(self.path).path
            # Streaming / binary routes (SSE, run file / zip / screenshot) send their own headers
            # then stream or range a body and handle client disconnects themselves, so they sit
            # outside the JSON-500 boundary (BE-0264): once their headers are on the wire a fallback
            # `_json` would double-write the response. Everything else dispatches under the boundary.
            if self._serve_streaming_get(path):
                return
            try:
                self._dispatch_get(path)
            except Exception as exc:
                self._respond_uncaught(exc)

        def _serve_streaming_get(self, path: str) -> bool:
            """Dispatch the streaming/binary GET routes kept outside do_GET's JSON-500 boundary
            (BE-0264); returns True when it handled the request.

            Order mirrors the old match: the `/api/jobs/<id>/events` SSE stream before the
            non-streaming `/api/jobs/<id>` view (which stays under the boundary), and the
            `.../archive.zip` route before the generic run-file serve.
            """
            if path.startswith("/api/jobs/") and path.endswith("/events"):
                self._sse_job(path[len("/api/jobs/") : -len("/events")])
            elif path.startswith("/runs/") and path.endswith("/archive.zip"):
                self._serve_run_archive(unquote(path[len("/runs/") : -len("/archive.zip")]))
            elif path == "/api/capture/screenshot":
                self._serve_capture_screenshot()
            elif path.startswith("/runs/"):
                self._serve_run_file(unquote(path[len("/runs/") :]))
            else:
                return False
            return True

        def _dispatch_registry(self, method: str, path: str, body: dict[str, Any]) -> None:
            """Dispatch a uniform (JSON or text) route from the shared registry (BE-0253).

            `off_loop` routes write their own responses and are handled bespoke by the caller
            before this runs, so an unmatched path — or a match that carries no handle — is the
            same not-found the old per-backend `match` fell through to.
            """
            matched = match_route(ROUTES, method, path)
            if matched is None:
                self._json({"error": "not found"}, 404)
                return
            route, params = matched
            if route.handle is None:
                self._json({"error": "not found"}, 404)
                return
            ctx = _StdlibCtx(params, body, self._qs, self._actor)
            payload, code = route.handle(state, ctx)
            if route.content_type is not None:
                self._text(payload, code, route.content_type)
            else:
                self._json(payload, code)

        def _dispatch_get(self, path: str) -> None:
            # These GET routes write their own responses (off_loop), so they stay bespoke here:
            # the index render, the ES-module frontend (BE-0247, a static-asset family whose two
            # backends use different path conventions — folded into the registry in a later slice),
            # and the OAuth round-trip. The streaming/binary GETs are already handled by
            # `_serve_streaming_get` before this. Every other GET dispatches from the registry.
            if path in ("/", "/index.html"):
                body = _index_html(state.themes_dir, state.default_theme).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in _MODULE_PATHS:
                self._serve_module(path[1:])
                return
            if path == "/api/oauth/login":
                self._oauth_login()
                return
            if path == "/api/oauth/callback":
                self._oauth_callback()
                return
            self._dispatch_registry("GET", path, {})

        def _csrf_ok(self) -> bool:
            return gate.csrf_ok(self.headers.get("Origin"), self.headers.get("Host") or "")

        def _host_ok(self) -> bool:
            return gate.host_allowed(state.allowed_hosts, self.headers.get("Host", ""))

        def do_POST(self) -> None:
            oplog.bind_request(oplog.new_request_id())
            if not self._gate():
                return
            path = urlparse(self.path).path
            # A bundle upload (BE-0073) carries a raw zip body, not JSON, and can be large — handle
            # it before the JSON read so it streams to disk instead of loading into memory. Same for
            # a single independently-uploaded artifact (BE-0268).
            if path == "/api/upload":
                self._handle_upload()
                return
            if path in _ARTIFACT_UPLOAD_PATHS:
                self._handle_artifact_upload(_ARTIFACT_UPLOAD_PATHS[path])
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
            try:
                self._dispatch_post(path, body)
            except Exception as exc:
                self._respond_uncaught(exc)

        def _dispatch_post(self, path: str, body: dict[str, Any]) -> None:
            # Login writes a Set-Cookie (off_loop) and needs the parsed body, so it stays bespoke;
            # the raw-body uploads are handled in do_POST before the JSON read. Everything else
            # dispatches from the shared registry — an op that raises is still caught by do_POST's
            # boundary and turned into a JSON 500 (BE-0264).
            if path == "/api/login":
                self._post_login(body)
                return
            self._dispatch_registry("POST", path, body)

        def do_DELETE(self) -> None:
            # DELETE surfaces: deregister a project (BE-0225) and delete a run/crawl-run (BE-0239).
            # Each mutates server state, so it runs behind the same auth gate as every route and the
            # same unconditional cross-origin block as do_POST (BE-0121) — a DELETE is as
            # CSRF-sensitive as a POST.
            oplog.bind_request(oplog.new_request_id())
            if not self._gate():
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not self._csrf_ok():
                if length:
                    self.rfile.read(length)  # drain so keep-alive isn't left with unread bytes
                self._json({"error": "cross-origin request blocked"}, 403)
                return
            # The DELETE routes (`?purge=true` skips the trash window, admin-only and gated in the
            # operation since the query isn't visible to the path-based RBAC; crawl and scenario runs
            # share the `runs/<id>/` tree, so one operation serves both, BE-0239) dispatch from the
            # shared registry like GET/POST.
            self._dispatch_registry("DELETE", urlparse(self.path).path, {})

        def _stream_bounded_body(self) -> BoundedZipReceiver | None:
            """Stream a raw POST body into a bounded, sha256-hashing temp file (`BoundedZipReceiver`,
            shared by `_handle_upload` (BE-0073) and `_handle_artifact_upload` (BE-0268)) — the size
            cap is enforced both up front (Content-Length) and while reading, so a lying length can't
            overrun it. Writes the JSON error response itself and returns None on any failure —
            cross-origin block, a missing/oversized Content-Length, a short read, or a mid-stream
            cap/OS error — cleaning the receiver up before returning; a caller only gets a receiver
            back on success, and owns its `cleanup()` from there."""
            # These early returns don't read the (possibly huge) body, so the connection still holds
            # the unread upload bytes. Close it rather than draining gigabytes or leaving them to
            # corrupt the next request on a keep-alive connection.
            if not self._csrf_ok():  # unconditional cross-origin block (BE-0121)
                self.close_connection = True
                self._json({"error": "cross-origin request blocked"}, 403)
                return None
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._json({"error": "empty upload"}, 400)
                return None
            if length > MAX_UPLOAD_BYTES:
                self.close_connection = True
                self._json({"error": f"upload too large (max {MAX_UPLOAD_BYTES} bytes)"}, 413)
                return None
            receiver = BoundedZipReceiver()
            try:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(_UPLOAD_CHUNK, remaining))
                    if not chunk:
                        break  # client closed early; `remaining > 0` below catches the short read
                    remaining -= len(chunk)
                    receiver.write(chunk)
                if remaining > 0:
                    # Body ended before Content-Length: a truncated upload. Fail explicitly (don't
                    # hand a partial zip downstream as an "invalid bundle"), and close the connection
                    # since its framing is now unreliable.
                    self.close_connection = True
                    self._json({"error": "upload incomplete (body ended early)"}, 400)
                    receiver.cleanup()
                    return None
                return receiver
            except UploadTooLarge:
                # Belt-and-suspenders: length <= MAX_UPLOAD_BYTES is checked above, so this loop
                # never actually exceeds the cap — kept only so the shared receiver's contract holds
                # for both backends alike.
                self.close_connection = True
                self._json({"error": f"upload too large (max {MAX_UPLOAD_BYTES} bytes)"}, 413)
            except OSError:
                self.close_connection = True
                self._json({"error": "upload interrupted"}, 400)
            receiver.cleanup()
            return None

        def _handle_upload(self) -> None:
            """Stream a raw-body zip upload to a temp file (bounded), then bind it as the active config
            (BE-0073). Raw body (`Content-Type: application/zip`, filename via `?name=`), not
            multipart: the SPA controls the request, and a streamed body needs no parser — the first
            POST here that doesn't read a JSON body."""
            filename = self._qs("name") or "bundle.zip"
            receiver = self._stream_bounded_body()
            if receiver is None:
                return
            # This raw-body route dispatches before do_POST's JSON-500 boundary (it reads the body
            # itself), so it needs its own: a raise from `bind_upload_config` — not the streaming
            # errors `_stream_bounded_body` already turns into JSON — would otherwise hit the
            # empty-body drop BE-0264 exists to eliminate (#1089).
            try:
                self._json(
                    *ops.bind_upload_config(
                        state,
                        receiver.path,
                        filename,
                        sha256=receiver.digest(),
                        actor=self._actor(),
                    )
                )
            except Exception as exc:
                self._respond_uncaught(exc)
            finally:
                receiver.cleanup()

        def _handle_artifact_upload(self, kind: ArtifactKind) -> None:
            """Stream one independently-uploaded artifact (BE-0268: `config` / `scenarios` /
            `binary`) to a temp file (bounded), then store it (`ops.bind_artifact`). Raw body, no
            `?name=` — an artifact's provenance name lives on the *composition*'s project record,
            not the artifact itself."""
            receiver = self._stream_bounded_body()
            if receiver is None:
                return
            # Same as `_handle_upload`: this raw-body route sits before do_POST's boundary, so a
            # raise from `bind_artifact` gets its own JSON-500 conversion rather than dropping the
            # connection empty-bodied (BE-0264 follow-up on #1089).
            try:
                self._json(
                    *ops.bind_artifact(
                        state,
                        kind,
                        receiver.path,
                        sha256=receiver.digest(),
                        actor=self._actor(),
                    )
                )
            except Exception as exc:
                self._respond_uncaught(exc)
            finally:
                receiver.cleanup()

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
            Content-Disposition; a redirect relies on the signed URL's own disposition. Honors a
            `Range` request (a report's `<video>` needs this to seek) with a 206/`Content-Range`
            reply, or 416 when the range isn't satisfiable."""
            if art is None:
                self._json({"error": "not found"}, 404)
                return
            if art.redirect is not None:  # a server store hands back a signed URL
                self.send_response(302)
                self.send_header("Location", art.redirect)
                self.end_headers()
                return
            data = art.body or b""
            status, chunk, headers = range_reply(data, self.headers.get("Range"))
            self.send_response(status)
            if status != 416:
                self.send_header("Content-Type", art.content_type)
                if filename is not None:
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)

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
    state.allowed_hosts = gate.allowed_hosts(host)
    return ThreadingHTTPServer((host, port), _make_handler(state))


# The SPA shell + its CSS/themes live in bajutsu/templates/serve.* — split out of this module so
# they read/edit as real files. The CSS/themes are still inlined into the one HTML response; the JS
# is served as ES modules from their own routes (BE-0247, see _JS_MODULES) rather than inlined.
# _TEMPLATE_DIR is imported from bajutsu.serve._paths (shared constant, avoids independent
# hand-counted .parent chains across modules at different package depths).


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


# The UI JS is split into section files (BE-0202), now native ES modules (BE-0247): each file
# `import`s what it needs and `export`s its public surface, so a file's dependencies are declared in
# the file itself. The page loads the entry module (serve.author.mjs) with <script type="module">;
# the browser then fetches the rest as its import graph resolves — so, unlike the old inlined
# <script>, each module is served at its own URL below. Order here is no longer load-bearing (the
# import graph decides evaluation order); this is just the set of module files the server exposes.
_JS_MODULES = (
    "serve.core.mjs",
    "serve.panels.mjs",
    "serve.crawl.mjs",
    "serve.metrics.mjs",
    "serve.projects.mjs",
    "serve.author.mjs",
)
_JS_ENTRY = "serve.author.mjs"  # the module <script type="module"> loads; pulls in the rest
# Request paths the modules are served at, e.g. "/serve.core.mjs". A frozenset both bounds what
# _serve_module will read (no path traversal — only these exact names) and lets _gate treat them as
# open GETs, so the login UI's JS loads before auth exactly as the inlined <script> did.
_MODULE_PATHS = frozenset(f"/{name}" for name in _JS_MODULES)


# Cache every bundled asset the server reads: the JS modules plus serve.css / serve.themes.css /
# serve.html.j2. Size the cache off the module count so adding a module never silently starts
# evicting (the +3 covers the two CSS files and the HTML template).
@functools.lru_cache(maxsize=len(_JS_MODULES) + 3)
def _asset(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _script_json(value: object) -> str:
    """`value` as JSON safe to inline in a <script> (escape `<` so a theme name can't close it)."""
    return json.dumps(value).replace("<", "\\u003c")


@functools.lru_cache(maxsize=8)
def _index_html(themes_dir: Path | None = None, default_theme: str | None = None) -> str:
    # Drop-in themes are static for the process lifetime (BE-0191 unit 2), so the scan is folded
    # into this cached render — keyed on (themes_dir, default_theme), the only inputs that vary it.
    from bajutsu.serve import themes as _themes

    discovered = _themes.discover_themes(themes_dir)
    manifests = [*_themes.BUILTIN_THEMES, *(t.manifest for t in discovered)]
    if default_theme is not None and default_theme not in {m.id for m in manifests}:
        logging.getLogger(__name__).warning(
            "ui.default_theme %r does not match any registered theme id %s — "
            "the page will load unthemed",
            default_theme,
            sorted(m.id for m in manifests),
        )
    return (
        _env()
        .get_template("serve.html.j2")
        .render(
            css=_asset("serve.css"),
            themes_css=_asset("serve.themes.css") + "".join(t.css for t in discovered),
            themes=manifests,
            themes_json=_script_json(
                [{"id": m.id, "name": m.name, "kind": m.kind} for m in manifests]
            ),
            default_theme_json=_script_json(default_theme),
            # Only when --themes is configured is there a directory to upload a theme into; the
            # client uses this to reveal the "Upload to Server" button (BE-0191 unit 6).
            themes_writable_json=_script_json(themes_dir is not None),
            # The frontend loads as ES modules from their own routes (BE-0247), no longer inlined.
            js_entry=_JS_ENTRY,
            js_modules=_JS_MODULES,
        )
    )

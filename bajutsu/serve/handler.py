"""HTTP request handler for ``bajutsu serve``."""

from __future__ import annotations

import functools
import json
import os
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml
from jinja2 import Environment, FileSystemLoader

from bajutsu.anthropic_client import (
    BEDROCK_MODEL_ENV,
    PROVIDER_ENV,
    PROVIDERS,
    provider,
)
from bajutsu.config import load_config
from bajutsu.scenario import load_scenario_file
from bajutsu.serve.helpers import (
    _int,
    _scenario_path,
    app_build_info,
    crawl_command,
    list_apps,
    list_fs,
    list_scenarios,
    list_simulators,
    mask_secret,
    record_command,
    run_command,
    scenario_out_path,
    unique_scenario_path,
    valid_backend,
    valid_run_id,
    valid_udid,
)
from bajutsu.serve.jobs import ServeState, _scenarios_dir_for, cancel_job

# The one secret the WebUI lets you set; the AI paths (record, --dismiss-alerts) read it.
_API_KEY_VAR = "ANTHROPIC_API_KEY"

# Session cookie set at login when a serve token is configured (BE-0051).
_SESSION_COOKIE = "bajutsu_session"


def _make_handler(state: ServeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def end_headers(self) -> None:
            # Standard hardening headers on every response (BE-0051): block MIME sniffing and
            # framing (clickjacking), and don't leak the URL via Referer.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def _json(self, payload: Any, code: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _sse(self, event: str, data: str) -> None:
            """Write one Server-Sent Event and flush it so the browser sees it live."""
            self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
            self.wfile.flush()

        def _sse_job(self, job_id: str) -> None:
            """Stream a job's log over SSE: a `log` event per line (backlog + live from the
            LogBus), then a terminal `done` event carrying the job's final view. The buffered bus
            means a subscriber that attaches after the job finished still replays everything."""
            job = state.jobs.get(job_id)
            if job is None:
                self._json({"error": "no such job"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")  # don't let a proxy buffer the stream
            self.end_headers()
            try:
                for line in state.logbus.stream(job_id):
                    self._sse("log", line)
                self._sse("done", json.dumps(job.view()))
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
            if self.command == "GET" and path in ("/", "/index.html"):
                return True
            if self.command == "POST" and path == "/api/login":
                return True
            if self._authorized():
                return True
            # Drain any request body before replying, so a keep-alive connection isn't left with
            # unread bytes that would corrupt the next request on it.
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._json({"error": "unauthorized"}, 401)
            return False

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
                    qs = parse_qs(urlparse(self.path).query)
                    scn_dir = _scenarios_dir_for(state, next(iter(qs.get("app") or []), None))
                    self._json(list_scenarios(scn_dir) if scn_dir else [])
                case "/api/apps":
                    self._json(list_apps(state.config) if state.config else [])
                case "/api/config":
                    self._json(
                        {
                            "config": str(state.config) if state.config else None,
                            "hasConfig": state.config is not None,
                            "root": str(state.root.resolve()),
                        }
                    )
                case "/api/fs":
                    qs = parse_qs(urlparse(self.path).query)
                    try:
                        self._json(list_fs(state.root, next(iter(qs.get("dir") or []), None)))
                    except (ValueError, OSError) as e:
                        self._json({"error": str(e)}, 400)
                case "/api/apikey":
                    qs = parse_qs(urlparse(self.path).query)
                    self._get_api_key(reveal=bool(next(iter(qs.get("reveal") or []), "")))
                case "/api/provider":
                    self._get_provider()
                case "/api/simulators":
                    self._json(list_simulators(state.simctl))
                case "/api/runs":
                    self._json(state.artifacts.list_runs())
                case "/api/scenario":
                    qs = parse_qs(urlparse(self.path).query)
                    scn_dir = _scenarios_dir_for(state, next(iter(qs.get("app") or []), None))
                    target = (
                        _scenario_path(scn_dir, next(iter(qs.get("path") or []), None))
                        if scn_dir
                        else None
                    )
                    if target is None or not target.is_file():
                        self._json({"error": "not found"}, 404)
                    else:
                        self._json({"yaml": target.read_text(encoding="utf-8")})
                case _ if path.startswith("/api/jobs/") and path.endswith("/events"):
                    self._sse_job(path[len("/api/jobs/") : -len("/events")])
                case _ if path.startswith("/api/jobs/"):
                    job = state.jobs.get(path[len("/api/jobs/") :])
                    self._json(job.view() if job else {"error": "no such job"}, 200 if job else 404)
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
                # Handlers below treat the body as a mapping; reject a non-object JSON (a list,
                # string, number) here rather than 500 on a `.get(...)`.
                self._json({"error": "expected a JSON object"}, 400)
                return
            match path:
                case "/api/login":
                    self._post_login(body)
                case "/api/config":
                    self._post_config(body)
                case "/api/apikey":
                    self._post_api_key(body)
                case "/api/provider":
                    self._post_provider(body)
                case "/api/run":
                    self._post_run(body)
                case "/api/record":
                    self._post_record(body)
                case "/api/crawl":
                    self._post_crawl(body)
                case "/api/scenario":
                    self._post_scenario(body)
                case "/api/approve":
                    self._post_approve(body)
                case _ if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                    job = state.jobs.get(path[len("/api/jobs/") : -len("/cancel")])
                    if job is None:
                        self._json({"error": "no such job"}, 404)
                    else:
                        self._json({"cancelled": cancel_job(job)})
                case _:
                    self._json({"error": "not found"}, 404)

        # Concrete picked devices are booted (and waited on) before a run/record; the "booted"
        # alias names whatever is already up, so it is not a boot target.
        @staticmethod
        def _boot_targets(udid: str) -> list[str]:
            return [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]

        def _post_login(self, body: dict[str, Any]) -> None:
            """Exchange the shared token for a session cookie (BE-0051). The token is sent in the
            POST body (never a URL), and the response sets an HttpOnly, SameSite cookie holding an
            opaque session id — so the token itself is never stored in the browser."""
            if not state.check_token(str(body.get("token", "") or "")):
                self._json({"error": "invalid token"}, 401)
                return
            sid = state.issue_session()
            payload = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Set-Cookie", f"{_SESSION_COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/"
            )
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _post_config(self, body: dict[str, Any]) -> None:
            """Bind a config.yml chosen in the UI's file browser.  The body carries a path the
            browser surfaced (confined to ``--root``); we validate it loads, then re-point
            ``state.config`` so apps/scenarios come from it."""
            raw = str(body.get("path", "") or "")
            if not raw:
                self._json({"error": "path is required"}, 400)
                return
            target = (state.root / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
            base = state.root.resolve()
            if target != base and base not in target.parents:
                self._json({"error": "path is outside the browse root"}, 400)
                return
            if not target.is_file():
                self._json({"error": "config not found"}, 404)
                return
            try:
                load_config(target.read_text(encoding="utf-8"))
            except (OSError, ValueError, yaml.YAMLError) as e:
                self._json({"error": f"invalid config: {e}"}, 400)
                return
            state.config = target
            self._json({"ok": True, "config": str(target), "apps": list_apps(target)})

        def _get_api_key(self, reveal: bool) -> None:
            """Report whether a key is set in the serve process's environment, with a redacted
            preview.  ``?reveal=1`` adds the full value — only on explicit request, and this
            server binds to localhost."""
            key = os.environ.get(_API_KEY_VAR) or None
            payload: dict[str, Any] = {"set": key is not None}
            if key is not None:
                payload["masked"] = mask_secret(key)
                if reveal:
                    payload["value"] = key
            self._json(payload)

        def _post_api_key(self, body: dict[str, Any]) -> None:
            """Set the Claude API key in the serve process's environment for this session (an
            empty value clears it).  It is held in memory only — never written to disk — and
            spawned record/run jobs inherit it via the process environment.  It is not persisted,
            so it must be re-entered after a restart (or set a real ``ANTHROPIC_API_KEY`` /
            ``.env`` for the serve process to pick up at startup)."""
            value = str(body.get("value", "") or "").strip()
            if value and any(c.isspace() for c in value):
                self._json({"error": "the API key must not contain whitespace"}, 400)
                return
            if value:
                os.environ[_API_KEY_VAR] = value
                self._json({"ok": True, "set": True, "masked": mask_secret(value)})
            else:
                os.environ.pop(_API_KEY_VAR, None)
                self._json({"ok": True, "set": False})

        def _get_provider(self) -> None:
            """Report the AI provider spawned jobs will use, with the Bedrock region/model.  Read
            from the serve process's environment (where ``_post_provider`` writes them), so it
            reflects what a record/crawl job inherits."""
            self._json(
                {
                    "provider": provider(),
                    "region": os.environ.get("AWS_REGION", ""),
                    "model": os.environ.get(BEDROCK_MODEL_ENV, ""),
                }
            )

        def _post_provider(self, body: dict[str, Any]) -> None:
            """Select the AI provider for spawned record/crawl jobs: the Anthropic API or Amazon
            Bedrock.  Written into the serve process's environment for this session only — never to
            disk — and inherited by jobs, mirroring the API-key handler.  Bedrock authenticates with
            the standard AWS credential chain (env / profile / role), so only the provider, region,
            and model id are set here; AWS credentials come from the serve environment."""
            prov = str(body.get("provider", "") or "").strip().lower()
            if prov not in PROVIDERS:
                self._json({"error": f"unknown provider: {prov or '(empty)'}"}, 400)
                return
            if prov == "anthropic":
                os.environ[PROVIDER_ENV] = "anthropic"
                self._json({"ok": True, "provider": "anthropic"})
                return
            # Bedrock needs a provider-prefixed model id (the bare Anthropic id is invalid there);
            # region is optional and falls back to AWS_REGION already in the environment.
            model = str(body.get("model", "") or "").strip()
            region = str(body.get("region", "") or "").strip()
            if not model:
                self._json({"error": "a Bedrock model id is required"}, 400)
                return
            if any(c.isspace() for c in model) or any(c.isspace() for c in region):
                self._json({"error": "region and model must not contain whitespace"}, 400)
                return
            os.environ[PROVIDER_ENV] = "bedrock"
            os.environ[BEDROCK_MODEL_ENV] = model
            if region:
                os.environ["AWS_REGION"] = region
            self._json({"ok": True, "provider": "bedrock", "region": region, "model": model})

        def _post_run(self, body: dict[str, Any]) -> None:
            cfg = state.config
            if cfg is None:
                self._json({"error": "open a config first"}, 400)
                return
            if not body.get("scenario") or not body.get("app"):
                self._json({"error": "scenario and app are required"}, 400)
                return
            app = str(body["app"])
            # Confine the scenario to the app's own scenarios dir: a serve client must not be able
            # to run an arbitrary file path on the host (BE-0051 / BE-0015 / BE-0016 prerequisite).
            scn_dir = _scenarios_dir_for(state, app)
            if scn_dir is None:
                self._json({"error": f"app '{app}' has no scenarios dir"}, 400)
                return
            # Match the client value against the dir's actual scenario files by name: the path we
            # use comes from enumerating the dir (trusted), never from the client string, so no
            # client-controlled value reaches a filesystem path. The UI's value works whether it
            # sends the file name or its full path (we compare on the basename).
            name = Path(str(body["scenario"])).name
            target = next(
                (p for p in scn_dir.glob("*.yaml") if p.name == name and p.is_file()), None
            )
            if target is None:
                self._json(
                    {"error": "scenario must be an existing .yaml inside the app's scenarios dir"},
                    400,
                )
                return
            backend = str(body.get("backend", "") or "")
            if backend and not valid_backend(backend):
                self._json({"error": f"unknown backend: {backend}"}, 400)
                return
            udid = str(body.get("udid", "") or "")
            if udid and not valid_udid(udid):
                self._json({"error": "invalid udid"}, 400)
                return
            cmd = run_command(
                str(target),
                app,
                backend=backend,
                udid=udid,
                workers=_int(body.get("workers"), 1),
                erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
                dismiss_alerts=body["dismissAlerts"]
                if isinstance(body.get("dismissAlerts"), bool)
                else None,
                config=str(cfg),
                baselines=str(state.baselines_dir),
            )
            app_path, build = app_build_info(cfg, app)
            # Atomic count + create so concurrent dispatches can't both slip past the cap.
            job = state.try_new_job(
                cmd, udids=self._boot_targets(udid), app_path=app_path, build=build
            )
            if job is None:
                self._json({"error": "too many concurrent jobs; try again shortly"}, 429)
                return
            state.executor.dispatch(state, job)
            self._json({"jobId": job.id})

        def _post_record(self, body: dict[str, Any]) -> None:
            """Author a scenario from a natural-language goal (the Record tab).  The authored
            file lands in the selected app's configured scenarios dir."""
            cfg = state.config
            if cfg is None:
                self._json({"error": "open a config first"}, 400)
                return
            if not body.get("goal") or not body.get("app"):
                self._json({"error": "goal and app are required"}, 400)
                return
            scn_dir = _scenarios_dir_for(state, str(body["app"]))
            if scn_dir is None:
                self._json({"error": f"app '{body['app']}' has no scenarios dir"}, 400)
                return
            scn_dir.mkdir(parents=True, exist_ok=True)
            out = unique_scenario_path(
                scenario_out_path(scn_dir, str(body.get("name") or "generated"))
            )
            # Validate the device args the same way /api/run does (BE-0051): no free-text backend
            # or udid reaches the spawned `bajutsu record` argv. The output path is already
            # confined by scenario_out_path above.
            backend = str(body.get("backend", "") or "")
            if backend and not valid_backend(backend):
                self._json({"error": f"unknown backend: {backend}"}, 400)
                return
            udid = str(body.get("udid", "") or "")
            if udid and not valid_udid(udid):
                self._json({"error": "invalid udid"}, 400)
                return
            cmd = record_command(
                str(out),
                body["app"],
                str(body["goal"]),
                agent=body.get("agent", ""),
                backend=backend,
                udid=udid,
                erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
                dismiss_alerts=body["dismissAlerts"]
                if isinstance(body.get("dismissAlerts"), bool)
                else None,
                config=str(cfg),
            )
            app_path, build = app_build_info(cfg, body["app"])
            job = state.try_new_job(
                cmd,
                udids=self._boot_targets(udid),
                app_path=app_path,
                build=build,
                out_path=str(out),
            )
            if job is None:
                self._json({"error": "too many concurrent jobs; try again shortly"}, 429)
                return
            state.executor.dispatch(state, job)
            self._json({"jobId": job.id, "path": str(out)})

        def _post_crawl(self, body: dict[str, Any]) -> None:
            """Explore an app breadth-first and build a screen map (the Crawl tab).  The screen
            map is streamed into ``runs/<runId>/screenmap.json`` as the crawl advances; the
            returned ``runId`` lets the UI poll it and draw the graph live."""
            cfg = state.config
            if cfg is None:
                self._json({"error": "open a config first"}, 400)
                return
            if not body.get("app"):
                self._json({"error": "app is required"}, 400)
                return
            # Resume continues an existing run (a pruned branch tapped in the UI); otherwise a new run.
            resume_src = str(body.get("resumeSrc", "") or "")
            resume_key = str(body.get("resumeKey", "") or "")
            resuming = bool(resume_src and resume_key and body.get("runId"))
            run_id = (
                str(body["runId"]) if resuming else datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
            )
            # A resumed crawl takes runId from the client; reject anything but a safe path segment
            # so `runs_dir / run_id` (the crawl's --out) can't escape runs_dir (BE-0051).
            if resuming and not valid_run_id(run_id):
                self._json({"error": "invalid runId"}, 400)
                return
            # Validate the device args like /api/run and /api/record (BE-0051): no free-text
            # backend or udid reaches the spawned `bajutsu crawl` argv.
            backend = str(body.get("backend", "") or "")
            if backend and not valid_backend(backend):
                self._json({"error": f"unknown backend: {backend}"}, 400)
                return
            udid = str(body.get("udid", "") or "")
            if udid and not valid_udid(udid):
                self._json({"error": "invalid udid"}, 400)
                return
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
                cmd, udids=self._boot_targets(udid), app_path=app_path, build=build
            )
            if job is None:
                self._json({"error": "too many concurrent jobs; try again shortly"}, 429)
                return
            state.executor.dispatch(state, job)
            self._json({"jobId": job.id, "runId": run_id})

        def _post_scenario(self, body: dict[str, Any]) -> None:
            """Save an edited scenario back to its ``*.yaml`` (bounded to the app's scenarios dir)."""
            scn_dir = _scenarios_dir_for(state, str(body.get("app") or "") or None)
            target = _scenario_path(scn_dir, body.get("path")) if scn_dir else None
            if target is None:
                self._json({"error": "path must be a *.yaml under the scenarios dir"}, 400)
                return
            text = str(body.get("yaml", ""))
            try:
                load_scenario_file(text)
            except (ValueError, OSError, yaml.YAMLError) as e:
                self._json({"error": f"invalid scenario: {e}"}, 400)
                return
            target.write_text(text, encoding="utf-8")
            self._json({"ok": True, "path": str(target)})

        def _post_approve(self, body: dict[str, Any]) -> None:
            """Promote a run's captured screenshot to a `visual` baseline.

            Copies ``runs/<runId>/<sid>/visual-actual.png`` → ``baselines/<baseline>``. Both
            ends are resolved and confined to their roots so a crafted runId / sid / baseline
            can't read or write outside the runs / baselines directories."""
            run_id = str(body.get("runId") or "")
            sid = str(body.get("sid") or "")
            baseline = str(body.get("baseline") or "")
            if not run_id or not sid or not baseline:
                self._json({"error": "runId, sid and baseline are required"}, 400)
                return
            data = state.artifacts.open_bytes(f"{run_id}/{sid}/visual-actual.png")
            base_root = state.baselines_dir.resolve()
            dest = (state.baselines_dir / baseline).resolve()
            if data is None:
                self._json({"error": "no captured screenshot for this run"}, 404)
                return
            if base_root != dest.parent and base_root not in dest.parents:
                self._json({"error": "baseline path escapes the baselines dir"}, 400)
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            self._json({"ok": True, "baseline": baseline})

        def _serve_run_file(self, rel: str) -> None:
            art = state.artifacts.get(rel)
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
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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

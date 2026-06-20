"""HTTP request handler for ``bajutsu serve``."""

from __future__ import annotations

import functools
import json
import mimetypes
import os
import shutil
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml
from jinja2 import Environment, FileSystemLoader

from bajutsu.config import load_config
from bajutsu.scenario import load_scenario_file
from bajutsu.serve.helpers import (
    _int,
    _scenario_path,
    app_build_info,
    crawl_command,
    list_apps,
    list_fs,
    list_runs,
    list_scenarios,
    list_simulators,
    mask_secret,
    record_command,
    run_command,
    scenario_out_path,
    unique_scenario_path,
)
from bajutsu.serve.jobs import ServeState, _scenarios_dir_for, cancel_job, run_job

# The one secret the WebUI lets you set; the AI paths (record, --dismiss-alerts) read it.
_API_KEY_VAR = "ANTHROPIC_API_KEY"


def _make_handler(state: ServeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: Any, code: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
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
                case "/api/simulators":
                    self._json(list_simulators(state.simctl))
                case "/api/runs":
                    self._json(list_runs(state.runs_dir))
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
                case _ if path.startswith("/api/jobs/"):
                    job = state.jobs.get(path[len("/api/jobs/") :])
                    self._json(job.view() if job else {"error": "no such job"}, 200 if job else 404)
                case _ if path.startswith("/runs/"):
                    self._serve_run_file(unquote(path[len("/runs/") :]))
                case _:
                    self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            match path:
                case "/api/config":
                    self._post_config(body)
                case "/api/apikey":
                    self._post_api_key(body)
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

        def _post_run(self, body: dict[str, Any]) -> None:
            cfg = state.config
            if cfg is None:
                self._json({"error": "open a config first"}, 400)
                return
            if not body.get("scenario") or not body.get("app"):
                self._json({"error": "scenario and app are required"}, 400)
                return
            udid = str(body.get("udid", "") or "")
            cmd = run_command(
                body["scenario"],
                body["app"],
                backend=body.get("backend", ""),
                udid=udid,
                workers=_int(body.get("workers"), 1),
                erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
                dismiss_alerts=body["dismissAlerts"]
                if isinstance(body.get("dismissAlerts"), bool)
                else None,
                config=str(cfg),
                baselines=str(state.baselines_dir),
            )
            app_path, build = app_build_info(cfg, body["app"])
            job = state.new_job(cmd, udids=self._boot_targets(udid), app_path=app_path, build=build)
            threading.Thread(target=run_job, args=(state, job), daemon=True).start()
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
            udid = str(body.get("udid", "") or "")
            cmd = record_command(
                str(out),
                body["app"],
                str(body["goal"]),
                agent=body.get("agent", ""),
                backend=body.get("backend", ""),
                udid=udid,
                erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
                dismiss_alerts=body["dismissAlerts"]
                if isinstance(body.get("dismissAlerts"), bool)
                else None,
                config=str(cfg),
            )
            app_path, build = app_build_info(cfg, body["app"])
            job = state.new_job(
                cmd,
                udids=self._boot_targets(udid),
                app_path=app_path,
                build=build,
                out_path=str(out),
            )
            threading.Thread(target=run_job, args=(state, job), daemon=True).start()
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
            udid = str(body.get("udid", "") or "")
            cmd = crawl_command(
                str(body["app"]),
                out=str(state.runs_dir / run_id),
                agent=body.get("agent", ""),
                backend=body.get("backend", ""),
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
            job = state.new_job(cmd, udids=self._boot_targets(udid), app_path=app_path, build=build)
            threading.Thread(target=run_job, args=(state, job), daemon=True).start()
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
            runs_base = state.runs_dir.resolve()
            actual = (state.runs_dir / run_id / sid / "visual-actual.png").resolve()
            base_root = state.baselines_dir.resolve()
            dest = (state.baselines_dir / baseline).resolve()
            if runs_base not in actual.parents or not actual.is_file():
                self._json({"error": "no captured screenshot for this run"}, 404)
                return
            if base_root != dest.parent and base_root not in dest.parents:
                self._json({"error": "baseline path escapes the baselines dir"}, 400)
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(actual, dest)
            self._json({"ok": True, "baseline": baseline})

        def _serve_run_file(self, rel: str) -> None:
            base = state.runs_dir.resolve()
            target = (state.runs_dir / rel).resolve()
            if base not in target.parents or not target.is_file():
                self._json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
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

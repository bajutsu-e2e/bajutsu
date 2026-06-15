"""HTTP request handler for ``bajutsu serve``."""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from bajutsu.config import load_config
from bajutsu.scenario import load_scenario_file
from bajutsu.serve.helpers import (
    _int,
    _scenario_path,
    app_build_info,
    list_apps,
    list_fs,
    list_runs,
    list_scenarios,
    list_simulators,
    record_command,
    run_command,
    scenario_out_path,
    unique_scenario_path,
)
from bajutsu.serve.jobs import ServeState, _scenarios_dir_for, cancel_job, run_job


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
                    body = INDEX_HTML.encode("utf-8")
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
                    self._json(
                        job.view() if job else {"error": "no such job"}, 200 if job else 404
                    )
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
                case "/api/run":
                    self._post_run(body)
                case "/api/record":
                    self._post_record(body)
                case "/api/scenario":
                    self._post_scenario(body)
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
            )
            app_path, build = app_build_info(cfg, body["app"])
            job = state.new_job(
                cmd, udids=self._boot_targets(udid), app_path=app_path, build=build
            )
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


def make_server(
    state: ServeState, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _make_handler(state))


INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>bajutsu</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--line:#334155;--fg:#e2e8f0;--mut:#94a3b8;--acc:#38bdf8;--ok:#22c55e;--ng:#ef4444;--run:#f59e0b}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#0b1220;color:var(--fg)}
header{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:1rem;padding:.55rem 1rem;background:var(--bg);border-bottom:1px solid var(--line)}
header .brand{font-weight:700}
header .mut{font-weight:400;color:var(--mut);font-size:.85em}
.toptabs{display:flex;gap:.3rem;margin-left:auto}
.toptab{background:#0b1220;border:1px solid var(--line);color:var(--mut);padding:.35rem 1.1rem;font:inherit;font-weight:600;border-radius:8px;cursor:pointer}
.toptab:hover{color:var(--fg)}
.toptab.active{color:#082f49;background:var(--acc);border-color:var(--acc)}
main{display:grid;grid-template-columns:340px minmax(300px,360px) 1fr;gap:1rem;padding:1rem;align-items:start}
main[hidden]{display:none}
/* Record view: form on the left, the log + generated-YAML panels stacked vertically beside it. */
#view-record{grid-template-columns:340px 1fr}
.rec-stack{display:flex;flex-direction:column;gap:1rem;height:calc(100vh - 6rem)}
.rec-stack>.logpanel,.rec-stack>.yamlpanel{flex:1;min-height:0;height:auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem}
label{display:block;margin:.6rem 0 .2rem;color:var(--mut);font-size:.85em}
select,input[type=text],input[type=number],textarea{width:100%;padding:.45rem;background:#0b1220;color:var(--fg);border:1px solid var(--line);border-radius:6px;font:inherit}
textarea.goal{min-height:5.5rem;resize:vertical;line-height:1.4}
.row{display:flex;gap:1rem}.row>div{flex:1}
.subhint{color:var(--mut);font-size:.78em;margin:.3rem 0 0;line-height:1.35}
.sims{max-height:24vh;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:.25rem}
.sims label{display:flex;align-items:center;gap:.45rem;margin:0;padding:.22rem .35rem;color:var(--fg);cursor:pointer;font-size:.9em;border-radius:4px}
.sims label:hover{background:#0b1220}
.sims .rt{color:var(--mut);font-size:.85em;margin-left:auto;white-space:nowrap}
.sims .empty{color:var(--mut);padding:.35rem}
.dot.off{background:var(--mut);opacity:.5}
.checks{display:flex;flex-direction:column;gap:.7rem;margin-top:.7rem}
.checks label{display:block;margin:0;color:var(--fg);cursor:pointer}
.checks .hint{display:block;color:var(--mut);font-size:.78em;line-height:1.35;margin:.15rem 0 0 1.45rem}
button.run{flex:0 0 auto;width:100%;padding:.6rem;background:var(--acc);color:#082f49;border:0;border-radius:6px;font-weight:700;cursor:pointer}
button.run:disabled{opacity:.5;cursor:default}
/* While a job runs the button turns amber, shows a spinner, and stays disabled (Stop aborts it). */
button.run.running{background:var(--run);color:#1c1402;opacity:1;cursor:default}
button.run.running::before{content:"";display:inline-block;width:.9em;height:.9em;margin-right:.5em;vertical-align:-.12em;border:2px solid rgba(28,20,2,.35);border-top-color:#1c1402;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
button.stop{flex:0 0 auto;width:100%;margin-top:.5rem;padding:.6rem;background:var(--ng);color:#fff;border:0;border-radius:6px;font-weight:700;cursor:pointer}
button.stop:disabled{opacity:.5;cursor:default}
button.stop[hidden]{display:none}
.status{flex:0 0 auto;font-weight:600}.status:not(:empty){margin-top:.6rem}.status.ok{color:var(--ok)}.status.ng{color:var(--ng)}.status.run{color:var(--acc)}
.logpanel{height:calc(100vh - 6rem);display:flex;flex-direction:column;overflow:hidden}
pre.out{flex:1;min-height:0;margin:.6rem 0 0;overflow:auto;background:#0b1220;border:1px solid var(--line);border-radius:6px;padding:.5rem;font-size:12px;white-space:pre-wrap}
pre.out:empty::before{content:attr(data-empty);color:var(--mut)}
.report{height:calc(100vh - 6rem)}iframe{width:100%;height:100%;border:1px solid var(--line);border-radius:10px;background:#fff}
.empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--mut);text-align:center;padding:1rem}
.names{color:var(--mut);font-size:.8em;margin-top:.2rem;min-height:1em}
.names .finfo{color:var(--fg);font-size:1.05em;margin-bottom:.25rem}
.scnlist{list-style:none;margin:.1rem 0 0;padding:0;font-size:1em;max-height:30vh;overflow:auto}
.scnlist li{padding:.12rem 0;color:var(--fg)}
.scnlist .sd{color:var(--mut)}
.left{display:flex;flex-direction:column;gap:1rem;height:calc(100vh - 6rem)}
.left>.card{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.left>.card>.panel{flex:1;min-height:0;overflow-y:auto}
.tabs{flex:0 0 auto;display:flex;gap:.25rem;margin:-.25rem 0 .9rem;border-bottom:1px solid var(--line)}
.tab{flex:1;background:none;border:0;border-bottom:2px solid transparent;color:var(--mut);padding:.5rem .3rem;font:inherit;font-weight:600;cursor:pointer}
.tab:hover{color:var(--fg)}.tab.active{color:var(--fg);border-bottom-color:var(--acc)}
.panel[hidden]{display:none}
.hhead{display:flex;justify-content:space-between;align-items:center;font-weight:600;margin-bottom:.5rem}
.refresh{background:none;border:1px solid var(--line);color:var(--mut);border-radius:6px;cursor:pointer;padding:.1rem .45rem}
.history{list-style:none;margin:0;padding:0;max-height:calc(100vh - 11rem);overflow:auto}
.history li{display:flex;align-items:center;gap:.5rem;padding:.4rem .5rem;border-radius:6px;cursor:pointer}
.history li:hover{background:#0b1220}
.history li.sel{background:#0b1220;outline:1px solid var(--acc)}
.history li.muted{cursor:default}
.history li.muted:hover{background:none}
.dot{width:.6rem;height:.6rem;border-radius:50%;flex:0 0 auto}.dot.ok{background:var(--ok)}.dot.ng{background:var(--ng)}
.hid{font-variant-numeric:tabular-nums}
.hsum{color:var(--mut);font-size:.8em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.muted{color:var(--mut)}
.yamlpanel{height:calc(100vh - 6rem);display:flex;flex-direction:column}
.yamlhead{display:flex;align-items:center;gap:.6rem;margin-bottom:.5rem}
.yamlhead .ttl{font-weight:600}
.yamlhead .savebtn{margin-left:auto;background:var(--acc);color:#082f49;border:0;border-radius:6px;font-weight:700;padding:.35rem .9rem;cursor:pointer}
.yamlhead .savebtn:disabled{opacity:.4;cursor:default}
textarea.yaml{flex:1;min-height:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;white-space:pre;overflow:auto;tab-size:2}
.cfgbtn{background:#0b1220;border:1px solid var(--line);color:var(--acc);padding:.3rem .8rem;font:inherit;font-weight:600;border-radius:8px;cursor:pointer}
.cfgbtn:hover{color:var(--fg)}
header .cfgname{color:var(--mut);font-size:.82em;max-width:32vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal{position:fixed;inset:0;z-index:50;background:rgba(2,6,23,.7);display:flex;align-items:center;justify-content:center}
.modal[hidden]{display:none}
.fsbox{width:min(560px,92vw);max-height:80vh;display:flex;flex-direction:column;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1rem}
.fshead{display:flex;align-items:center;margin-bottom:.6rem}.fshead .ttl{font-weight:700}
.fshead .fsclose{margin-left:auto;background:none;border:0;color:var(--mut);font-size:1.1em;cursor:pointer}
.fspath{color:var(--mut);font-size:.82em;margin-bottom:.4rem;word-break:break-all}
.fslist{list-style:none;margin:0;padding:0;overflow:auto;flex:1;border:1px solid var(--line);border-radius:8px}
.fslist li{padding:.4rem .6rem;cursor:pointer;display:flex;gap:.5rem;align-items:center}
.fslist li:hover{background:#0b1220}
.fslist li .ic{width:1.2em;text-align:center}
.fslist li.file .nm{color:var(--acc)}
.fslist li.muted{color:var(--mut);cursor:default}.fslist li.muted:hover{background:none}
.fshint{color:var(--mut);font-size:.78em;margin-top:.5rem}
</style></head>
<body>
<header>
  <span class="brand">bajutsu</span>
  <span class="mut">natural-language authoring \u00b7 deterministic replay (Tier 1 \u2014 not the CI gate)</span>
  <button class="cfgbtn" id="opencfg">Open config</button>
  <span class="cfgname" id="cfgname"></span>
  <div class="toptabs">
    <button class="toptab active" data-view="record">Record</button>
    <button class="toptab" data-view="replay">Replay</button>
  </div>
</header>

<!-- ===== Record: author a scenario from a natural-language goal ===== -->
<main id="view-record">
  <div class="left">
    <div class="card">
      <div class="panel" id="panel-record">
        <label>Goal (natural language)</label>
        <textarea id="rec-goal" class="goal" placeholder="e.g. increment the counter twice and check it reads 2"></textarea>
        <label>App</label><select id="rec-app"></select>
        <label>Agent</label><select id="rec-agent">
          <option value="">default ($BAJUTSU_AGENT or api)</option>
          <option value="api">api (Anthropic API)</option>
          <option value="claude-code">claude-code (claude CLI)</option>
        </select>
        <label>Backend</label><input id="rec-backend" type="text" placeholder="idb">
        <label>Device</label>
        <div class="row" style="align-items:flex-end">
          <div><select id="rec-device"></select></div>
          <div style="flex:0 0 auto"><button class="refresh" id="rec-simrefresh" title="refresh devices">&#8635;</button></div>
        </div>
        <label>Save as</label>
        <input id="rec-name" type="text" placeholder="generated.yaml \u2014 leave blank to default">
        <div class="subhint">The recorded scenario is written under the scenarios dir, so it shows up in the <b>Replay</b> tab to run. Blank defaults to <code>generated.yaml</code>; if the name already exists, the run's date-time is appended (e.g. <code>generated-20260613-153045.yaml</code>) so nothing is overwritten.</div>
        <div class="checks">
          <label><input type="checkbox" id="rec-erase"> erase device first
            <span class="hint">Wipe the simulator before authoring (record's default) so the app starts from onboarding. Uncheck to author against the app's current state.</span></label>
          <label><input type="checkbox" id="rec-nodismiss"> disable alert-dismiss
            <span class="hint">The alert guard (Claude vision) dismisses unexpected system prompts while authoring; on by default (needs ANTHROPIC_API_KEY). Check to force it off.</span></label>
        </div>
      </div>
    </div>
  </div>
  <div class="rec-stack">
    <div class="card logpanel">
      <button class="run" id="rec-go" data-idle="Generate scenario">Generate scenario</button>
      <button class="stop" id="rec-stop" hidden>Stop</button>
      <div class="status" id="rec-status"></div>
      <pre class="out" id="rec-out" data-empty="Enter a goal and press Generate to watch the agent author it, turn by turn."></pre>
    </div>
    <div class="card yamlpanel" id="rec-yamlpanel">
      <div class="yamlhead">
        <span class="ttl">Generated scenario</span>
        <span class="muted" id="rec-yamlinfo" style="font-size:.8em"></span>
        <button class="savebtn" id="rec-save" disabled>Save</button>
      </div>
      <textarea id="rec-yaml" class="yaml" placeholder="The recorded scenario YAML appears here once authoring finishes \u2014 edit and Save, then run it in the Replay tab."></textarea>
    </div>
  </div>
</main>

<!-- ===== Replay: run a scenario and view its report ===== -->
<main id="view-replay" hidden>
  <div class="left">
    <div class="card">
      <div class="tabs">
        <button class="tab active" data-tab="run">Run</button>
        <button class="tab" data-tab="history" id="histtab">History</button>
      </div>
      <div class="panel" id="panel-run">
        <label>Scenario</label>
        <select id="scn"></select><div class="names" id="names"></div>
        <label>App</label><select id="app"></select>
        <div class="row"><div><label>Backend</label><input id="backend" type="text" placeholder="idb"></div>
          <div><label>Workers</label><input id="workers" type="number" min="1" value="1"></div></div>
        <div class="hhead"><span>Simulators</span><button class="refresh" id="simrefresh" title="refresh devices">&#8635;</button></div>
        <div class="sims" id="sims"></div>
        <div class="subhint">Pick the simulators to run on &mdash; shut-down ones are booted (and waited for) before the run. Pick two or more (Workers auto-tracks the count) to run the scenarios in parallel across them: each device gets its own network capture, video / device log, and setLocation / push. None picked = the already-booted device.</div>
        <div class="checks">
          <label><input type="checkbox" id="erasedev"> erase device first
            <span class="hint">Wipe the whole simulator (simctl erase &mdash; all apps, data, settings) before each scenario. Off (default) leaves it to each scenario's <code>preconditions.erase</code>; the app is reinstalled fresh either way.</span></label>
          <label><input type="checkbox" id="nodismiss"> disable alert-dismiss
            <span class="hint">The alert guard is on by default: Claude vision dismisses unexpected system prompts (Save Password, notification permission) that idb can't see or tap (needs ANTHROPIC_API_KEY). Check to force it off for this run; a scenario can also set <code>dismissAlerts</code> itself.</span></label>
        </div>
      </div>
      <div class="panel" id="panel-history" hidden>
        <div class="hhead"><span>Past runs</span><button class="refresh" id="refresh" title="refresh">&#8635;</button></div>
        <ul class="history" id="history"></ul>
      </div>
    </div>
  </div>
  <div class="card logpanel">
    <button class="run" id="go" data-idle="Run">Run</button>
    <button class="stop" id="stop" hidden>Stop</button>
    <div class="status" id="status"></div>
    <pre class="out" id="out" data-empty="Run a scenario to see its output here."></pre>
  </div>
  <div class="report" id="report"><div class="empty">Run a scenario to see its report here.</div></div>
</main>

<!-- ===== Open config: a server-side file browser confined to the serve --root ===== -->
<div class="modal" id="fsmodal" hidden>
  <div class="fsbox">
    <div class="fshead"><span class="ttl">Open config</span><button class="fsclose" id="fsclose">&#10005;</button></div>
    <div class="fspath" id="fspath"></div>
    <ul class="fslist" id="fslist"></ul>
    <div class="fshint">Pick a <code>.yml</code>/<code>.yaml</code> config file. Browsing is limited to the server's <code>--root</code>.</div>
  </div>
</div>
<script>
const $=s=>document.querySelector(s);
let poll=null,recPoll=null,selectedRun=null,recPath=null,scnFiles=[],apps=[],sims=[];
let recJobId=null,runJobId=null;
// Toggle a run/stop button pair between idle and running (amber + spinner via the .running class).
function setBusy(btn,stop,on,busyLabel){
  btn.classList.toggle('running',on);btn.disabled=on;btn.textContent=on?busyLabel:btn.dataset.idle;
  stop.hidden=!on;stop.disabled=false;stop.textContent='Stop';
}
// Ask the server to abort a running job; polling then sees it finish and resets the UI.
async function cancelJob(id,stop){
  if(!id)return;stop.disabled=true;stop.textContent='Stopping\u2026';
  try{await fetch('/api/jobs/'+id+'/cancel',{method:'POST'})}catch(e){}
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function setStatus(el,t,c){el.textContent=t;el.className='status '+c}

// ---- top-level Record / Replay views ----
function showView(name){
  document.querySelectorAll('.toptab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));
  $('#view-record').hidden=name!=='record';$('#view-replay').hidden=name!=='replay';
  if(name==='replay')loadHistory();
}
document.querySelectorAll('.toptab').forEach(t=>t.addEventListener('click',()=>showView(t.dataset.view)));

// ---- config: bound at startup or opened from the UI's file browser ----
async function loadConfig(){
  let c;try{c=await (await fetch('/api/config')).json()}catch(e){c={hasConfig:false}}
  $('#cfgname').textContent=c.hasConfig?c.config:'no config bound — open one →';
  if(c.hasConfig){await loadShared()}else{openFs()}
}
// Browse the server's --root for a config.yml. Paths returned by /api/fs are absolute and the
// server re-validates every one against --root, so clicking can never escape the browse ceiling.
async function browseFs(dir){
  let d;try{d=await (await fetch('/api/fs'+(dir?('?dir='+encodeURIComponent(dir)):''))).json()}catch(e){d={error:'failed'}}
  if(d.error){$('#fslist').innerHTML='<li class="muted">'+esc(d.error)+'</li>';return}
  $('#fspath').textContent=d.cwd;
  let h='';
  if(d.parent!=null)h+=`<li class="dir" data-dir="${esc(d.parent)}"><span class="ic">&#8593;</span><span class="nm">..</span></li>`;
  h+=d.dirs.map(n=>`<li class="dir" data-dir="${esc(d.cwd+'/'+n)}"><span class="ic">&#128193;</span><span class="nm">${esc(n)}</span></li>`).join('');
  h+=d.files.map(n=>`<li class="file" data-file="${esc(d.cwd+'/'+n)}"><span class="ic">&#128196;</span><span class="nm">${esc(n)}</span></li>`).join('');
  $('#fslist').innerHTML=h||'<li class="muted">empty</li>';
  $('#fslist').querySelectorAll('li[data-dir]').forEach(li=>li.addEventListener('click',()=>browseFs(li.dataset.dir)));
  $('#fslist').querySelectorAll('li[data-file]').forEach(li=>li.addEventListener('click',()=>chooseConfig(li.dataset.file)));
}
function openFs(){$('#fsmodal').hidden=false;browseFs('')}
function closeFs(){$('#fsmodal').hidden=true}
$('#opencfg').addEventListener('click',openFs);
$('#fsclose').addEventListener('click',closeFs);
$('#fsmodal').addEventListener('click',e=>{if(e.target===$('#fsmodal'))closeFs()});
async function chooseConfig(path){
  const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const d=await r.json();
  if(d.error){$('#fslist').innerHTML='<li class="muted">'+esc(d.error)+'</li>';return}
  $('#cfgname').textContent=d.config;closeFs();await loadShared();
}

// ---- shared data: apps, scenarios, simulators (used by both views) ----
async function loadShared(){
  try{apps=await (await fetch('/api/apps')).json()}catch(e){apps=[]}
  const opts=apps.map(a=>`<option>${esc(a)}</option>`).join('');
  $('#app').innerHTML=opts;$('#rec-app').innerHTML=opts;
  await loadScenarios();
}
// Scenarios come from the selected app's configured dir, so reload when the Replay app changes.
async function loadScenarios(){
  const app=$('#app').value;
  try{scnFiles=app?await (await fetch('/api/scenarios?app='+encodeURIComponent(app))).json():[]}catch(e){scnFiles=[]}
  $('#scn').innerHTML=scnFiles.map(s=>`<option value="${esc(s.path)}">${esc(s.file)}</option>`).join('');
  showInfo();
}
$('#app').addEventListener('change',loadScenarios);
async function loadSims(){
  try{sims=await (await fetch('/api/simulators')).json()}catch(e){sims=[]}
  // Replay: multi-select checkboxes (parallel pool).
  const el=$('#sims');
  el.innerHTML=sims.length?sims.map(s=>`<label><input type="checkbox" class="simck" value="${esc(s.udid)}"><span class="dot ${s.booted?'ok':'off'}" title="${s.booted?'booted':'shut down'}"></span><span>${esc(s.name)}</span><span class="rt">${esc(s.runtime)}${s.booted?'':' \u00b7 off'}</span></label>`).join(''):'<div class="empty">no simulators found</div>';
  el.querySelectorAll('.simck').forEach(c=>c.addEventListener('change',onSimChange));
  // Record: single-device dropdown ("booted" = whatever is already up).
  $('#rec-device').innerHTML='<option value="booted">booted (already up)</option>'+sims.map(s=>`<option value="${esc(s.udid)}">${esc(s.name)} \u00b7 ${esc(s.runtime)}${s.booted?'':' \u00b7 off'}</option>`).join('');
}

// ---- Record: author a scenario from a goal ----
$('#rec-simrefresh').addEventListener('click',loadSims);
$('#rec-go').addEventListener('click',async()=>{
  const goal=$('#rec-goal').value.trim();
  if(!goal){setStatus($('#rec-status'),'enter a goal first','ng');return}
  if(recPoll)clearInterval(recPoll);
  setBusy($('#rec-go'),$('#rec-stop'),true,'Authoring\u2026');$('#rec-out').textContent='';
  $('#rec-yaml').value='';$('#rec-save').disabled=true;$('#rec-yamlinfo').textContent='';recPath=null;
  setStatus($('#rec-status'),'','run');
  const r=await fetch('/api/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    goal,app:$('#rec-app').value,agent:$('#rec-agent').value,backend:$('#rec-backend').value.trim(),
    udid:$('#rec-device').value||'booted',name:$('#rec-name').value.trim()||undefined,
    erase:$('#rec-erase').checked,dismissAlerts:$('#rec-nodismiss').checked?false:undefined})});
  const {jobId,path,error}=await r.json();
  if(error){setStatus($('#rec-status'),error,'ng');setBusy($('#rec-go'),$('#rec-stop'),false);return}
  recPath=path;recJobId=jobId;
  recPoll=setInterval(()=>recCheck(jobId),1000);recCheck(jobId);
});
$('#rec-stop').addEventListener('click',()=>cancelJob(recJobId,$('#rec-stop')));
async function recCheck(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#rec-out').textContent=(j.lines||[]).join('\\n');$('#rec-out').scrollTop=$('#rec-out').scrollHeight;
  if(j.status==='running')return;
  clearInterval(recPoll);recPoll=null;recJobId=null;setBusy($('#rec-go'),$('#rec-stop'),false);
  if(j.cancelled){setStatus($('#rec-status'),'cancelled','ng');return}
  setStatus($('#rec-status'),j.ok?'authored \u2713':'failed', j.ok?'ok':'ng');
  if(j.ok&&(j.outPath||recPath)){await loadGenerated(j.outPath||recPath);loadScenarios();}
}
async function loadGenerated(path){
  recPath=path;
  try{
    const d=await (await fetch('/api/scenario?app='+encodeURIComponent($('#rec-app').value)+'&path='+encodeURIComponent(path))).json();
    if(d.yaml!=null){$('#rec-yaml').value=d.yaml;$('#rec-save').disabled=false;
      $('#rec-yamlinfo').textContent=path.split('/').pop();}
  }catch(e){}
}
$('#rec-save').addEventListener('click',async()=>{
  if(!recPath)return;
  $('#rec-save').disabled=true;$('#rec-save').textContent='Saving\u2026';
  const r=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({app:$('#rec-app').value,path:recPath,yaml:$('#rec-yaml').value})});
  const d=await r.json();
  $('#rec-save').textContent='Save';$('#rec-save').disabled=false;
  if(d.error){setStatus($('#rec-status'),d.error,'ng')}
  else{setStatus($('#rec-status'),'saved \u2713','ok');loadScenarios()}
});

// ---- Replay: scenario info, run, history ----
function showInfo(){
  const f=scnFiles.find(s=>s.path===$('#scn').value),el=$('#names');
  if(!f){el.innerHTML='';return}
  let h='';
  if(f.description)h+=`<div class="finfo">${esc(f.description)}</div>`;
  if(f.scenarios&&f.scenarios.length)h+='<ul class="scnlist">'+f.scenarios.map(s=>`<li><b>${esc(s.name)}</b>${s.description?' &mdash; <span class="sd">'+esc(s.description)+'</span>':''}</li>`).join('')+'</ul>';
  el.innerHTML=h;
}
$('#scn').addEventListener('change',showInfo);
function pickedUdids(){return [...$('#sims').querySelectorAll('.simck:checked')].map(c=>c.value)}
function onSimChange(){const n=pickedUdids().length;if(n>0)$('#workers').value=n}
$('#simrefresh').addEventListener('click',loadSims);
$('#go').addEventListener('click',async()=>{
  if(poll)clearInterval(poll);
  setBusy($('#go'),$('#stop'),true,'Running\u2026');$('#out').textContent='';
  setStatus($('#status'),'','run');
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    scenario:$('#scn').value,app:$('#app').value,backend:$('#backend').value.trim(),udid:pickedUdids().join(',')||'booted',
    workers:parseInt($('#workers').value,10)||1,
    erase:$('#erasedev').checked||undefined,dismissAlerts:$('#nodismiss').checked?false:undefined})});
  const {jobId,error}=await r.json();
  if(error){setStatus($('#status'),error,'ng');setBusy($('#go'),$('#stop'),false);return}
  runJobId=jobId;
  poll=setInterval(()=>check(jobId),1000);check(jobId);
});
$('#stop').addEventListener('click',()=>cancelJob(runJobId,$('#stop')));
async function check(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#out').textContent=(j.lines||[]).join('\\n');$('#out').scrollTop=$('#out').scrollHeight;
  if(j.status==='running')return;  // the Run button (amber + spinner) shows the running state
  clearInterval(poll);poll=null;runJobId=null;setBusy($('#go'),$('#stop'),false);
  if(j.cancelled){setStatus($('#status'),'cancelled','ng');loadHistory();return}
  setStatus($('#status'),j.ok?'PASS':'FAIL', j.ok?'ok':'ng');
  if(j.runId)setReport(j.runId);
  loadHistory();
}
function setReport(id){selectedRun=id;$('#report').innerHTML=`<iframe src="/runs/${id}/report.html"></iframe>`}
async function loadHistory(){
  let runs;try{runs=await (await fetch('/api/runs')).json()}catch(e){return}
  const tab=$('#histtab');if(tab)tab.textContent='History'+(runs.length?` (${runs.length})`:'');
  const ul=$('#history');
  if(!runs.length){ul.innerHTML='<li class="muted">no runs yet</li>';return}
  ul.innerHTML=runs.map(r=>`<li data-id="${r.id}"${r.id===selectedRun?' class="sel"':''}><span class="dot ${r.ok?'ok':'ng'}"></span><span class="hid">${r.id}</span><span class="hsum">${r.passed}/${r.total}${r.scenarios.length?' \u00b7 '+r.scenarios.join(', '):''}</span></li>`).join('');
  ul.querySelectorAll('li[data-id]').forEach(li=>li.addEventListener('click',()=>{setReport(li.dataset.id);ul.querySelectorAll('li').forEach(x=>x.classList.remove('sel'));li.classList.add('sel')}));
}
$('#refresh').addEventListener('click',loadHistory);
function showTab(name){
  document.querySelectorAll('#view-replay .tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  $('#panel-run').hidden=name!=='run';$('#panel-history').hidden=name!=='history';
  if(name==='history')loadHistory();
}
document.querySelectorAll('#view-replay .tab').forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.tab)));

loadConfig();
loadSims();
loadHistory();
setInterval(loadHistory,4000);
</script>
</body></html>"""

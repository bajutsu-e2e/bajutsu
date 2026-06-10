"""`bajutsu serve` — a local web UI to run scenarios and view their reports.

A Tier-1 convenience (authoring / operation), **never part of the CI gate**. The CLI run
pipeline and the self-contained `report.html` do the real work; this serves a small launcher
page, lists scenarios + apps, spawns `python -m bajutsu run ...` per request on a background
thread, streams its output, and serves the produced `runs/<id>/` tree so the report's relative
asset links resolve. Stdlib only — the same `ThreadingHTTPServer` approach as the network
collector ([[network]]).
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from bajutsu import env
from bajutsu.config import load_config
from bajutsu.scenario import load_scenario_file

# The run command prints "PASS/FAIL  runs/<id>/manifest.json"; pull <id> from it.
_RUN_ID_RE = re.compile(r"runs/([0-9A-Za-z._-]+)/manifest\.json")

Popen = Callable[..., Any]


# --- pure helpers (unit-tested without a server) ---


def list_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    """Every `*.yaml` under `scenarios_dir`: a path the run command can take, the file-level
    `description`, and each scenario's name + description (for the UI)."""
    out: list[dict[str, Any]] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        description: str | None = None
        scenarios: list[dict[str, Any]] = []
        try:
            sf = load_scenario_file(path.read_text(encoding="utf-8"))
            description = sf.description
            scenarios = [{"name": s.name, "description": s.description} for s in sf.scenarios]
        except (OSError, ValueError):
            pass
        out.append({
            "file": path.name, "path": str(path), "description": description,
            "scenarios": scenarios, "names": [s["name"] for s in scenarios],
        })
    return out


def list_apps(config_path: Path) -> list[str]:
    try:
        return sorted(load_config(config_path.read_text(encoding="utf-8")).apps)
    except (OSError, ValueError):
        return []


def list_simulators(simctl: env.RunFn = env._real_run) -> list[dict[str, Any]]:
    """Available simulators for the device picker (booted first): udid, name, runtime, booted.
    A run boots any picked-but-shut-down device first, so the UI can start from a cold list."""
    try:
        data = json.loads(simctl(env.list_devices_cmd(), None))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return []
    sims: list[dict[str, Any]] = []
    for runtime, devices in (data.get("devices") or {}).items():
        # "com.apple.CoreSimulator.SimRuntime.iOS-26-5" -> "iOS 26.5"
        label = runtime.split("SimRuntime.")[-1].replace("-", " ", 1).replace("-", ".")
        for d in devices:
            if not d.get("isAvailable", True) or not d.get("udid"):
                continue
            sims.append({
                "udid": str(d["udid"]), "name": str(d.get("name", "")),
                "runtime": label, "booted": d.get("state") == "Booted",
            })
    sims.sort(key=lambda s: (not s["booted"], s["name"]))
    return sims


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Past runs under `runs_dir` (newest first), each summarized from its manifest.json for
    the history list. Run ids are timestamps, so a reverse lexicographic sort is newest-first."""
    out: list[dict[str, Any]] = []
    if not runs_dir.is_dir():
        return out
    for d in runs_dir.iterdir():
        manifest = d / "manifest.json"
        if not (d.is_dir() and manifest.is_file()):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scenarios = [s for s in (data.get("scenarios") or []) if isinstance(s, dict)]
        out.append({
            "id": d.name,
            "ok": bool(data.get("ok")),
            "report": (d / "report.html").is_file(),
            "scenarios": [str(s.get("scenario", "")) for s in scenarios],
            "passed": sum(1 for s in scenarios if s.get("ok")),
            "total": len(scenarios),
        })
    out.sort(key=lambda r: r["id"], reverse=True)
    return out


def _int(value: Any, default: int) -> int:
    """Coerce a JSON value to int, falling back to `default` (e.g. for `workers`)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_command(
    scenario: str, app: str, *, backend: str = "", udid: str = "", workers: int = 1,
    erase: bool | None = None, dismiss_alerts: bool | None = None,
    config: str = "bajutsu.config.yaml",
) -> list[str]:
    """The `python -m bajutsu run ...` argv for a launch request. `udid` may be a comma list and
    `workers > 1` runs those devices as a parallel pool (capped to the pool size by the CLI).
    `erase` / `dismiss_alerts` are overrides: True/False force the flag on/off, None leaves each
    scenario's own preconditions.erase / dismissAlerts (the latter on by default) to decide."""
    cmd = [sys.executable, "-m", "bajutsu", "run", scenario, "--app", app, "--config", config]
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    if workers > 1:
        cmd += ["--workers", str(workers)]
    if erase is True:
        cmd += ["--erase"]
    elif erase is False:
        cmd += ["--no-erase"]
    if dismiss_alerts is True:
        cmd += ["--dismiss-alerts"]
    elif dismiss_alerts is False:
        cmd += ["--no-dismiss-alerts"]
    return cmd


# --- jobs ---


@dataclass
class Job:
    id: str
    cmd: list[str]
    udids: list[str] = field(default_factory=list)  # devices to boot before the run
    status: str = "running"      # running | done
    exit_code: int | None = None
    run_id: str | None = None    # the runs/<id> the run produced, parsed from its output
    lines: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def view(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id, "status": self.status, "exitCode": self.exit_code,
                "runId": self.run_id, "ok": self.exit_code == 0 if self.status == "done" else None,
                "lines": list(self.lines),
            }


@dataclass
class ServeState:
    scenarios_dir: Path
    config: Path
    runs_dir: Path
    cwd: Path = field(default_factory=Path.cwd)
    popen: Popen = subprocess.Popen
    simctl: env.RunFn = env._real_run  # runs `xcrun simctl …` (booting devices, listing them)
    jobs: dict[str, Job] = field(default_factory=dict)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def new_job(self, cmd: list[str], udids: list[str] | None = None) -> Job:
        with self._lock:
            self._seq += 1
            job = Job(id=str(self._seq), cmd=cmd, udids=list(udids or []))
            self.jobs[job.id] = job
        return job


def _spawn_env() -> dict[str, str]:
    """Ensure the venv bin dir (where the `idb` client lives) is on PATH for the run."""
    env = dict(os.environ)
    bindir = str(Path(sys.executable).parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def _log(job: Job, line: str) -> None:
    with job.lock:
        job.lines.append(line)


def _boot_devices(state: ServeState, job: Job) -> bool:
    """Boot the job's devices in parallel (each `bootstatus -b` boots its device and waits
    until ready) so multiple cold simulators come up at the same time, then the run drives
    them concurrently. Returns False and marks the job failed if any device won't boot."""
    if not job.udids:
        return True
    for udid in job.udids:
        _log(job, f"booting {udid}…")
    errors: dict[str, str] = {}
    errlock = threading.Lock()

    def boot(udid: str) -> None:
        try:
            state.simctl(env.bootstatus_cmd(udid), None)
            _log(job, f"booted {udid}")
        except (OSError, subprocess.CalledProcessError) as e:
            with errlock:
                errors[udid] = str(e)

    threads = [threading.Thread(target=boot, args=(u,), daemon=True) for u in job.udids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        for udid, msg in errors.items():
            _log(job, f"boot failed: {udid}: {msg}")
        with job.lock:
            job.exit_code = 1
            job.status = "done"
        return False
    return True


def run_job(state: ServeState, job: Job) -> None:
    """Boot the job's devices (if any), then run `job.cmd`, capturing combined output
    line-by-line and the produced run id."""
    if not _boot_devices(state, job):
        return
    proc = state.popen(
        job.cmd, cwd=str(state.cwd), env=_spawn_env(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    for raw in proc.stdout or []:
        line = raw.rstrip("\n")
        match = _RUN_ID_RE.search(line)
        with job.lock:
            job.lines.append(line)
            if match:
                job.run_id = match.group(1)
    proc.wait()
    with job.lock:
        job.exit_code = proc.returncode
        job.status = "done"


# --- HTTP ---


def _make_handler(state: ServeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: Any, code: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/scenarios":
                self._json(list_scenarios(state.scenarios_dir))
            elif path == "/api/apps":
                self._json(list_apps(state.config))
            elif path == "/api/simulators":
                self._json(list_simulators(state.simctl))
            elif path == "/api/runs":
                self._json(list_runs(state.runs_dir))
            elif path.startswith("/api/jobs/"):
                job = state.jobs.get(path[len("/api/jobs/"):])
                self._json(job.view() if job else {"error": "no such job"}, 200 if job else 404)
            elif path.startswith("/runs/"):
                self._serve_run_file(unquote(path[len("/runs/"):]))
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            if urlparse(self.path).path != "/api/run":
                self._json({"error": "not found"}, 404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            if not body.get("scenario") or not body.get("app"):
                self._json({"error": "scenario and app are required"}, 400)
                return
            udid = str(body.get("udid", "") or "")
            # Concrete picked devices are booted (and waited on) before the run; the "booted"
            # alias names whatever is already up, so it is not a boot target.
            boot = [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]
            cmd = run_command(
                body["scenario"], body["app"],
                backend=body.get("backend", ""), udid=udid,
                workers=_int(body.get("workers"), 1),
                erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
                dismiss_alerts=body["dismissAlerts"] if isinstance(body.get("dismissAlerts"), bool) else None,
                config=str(state.config),
            )
            job = state.new_job(cmd, udids=boot)
            threading.Thread(target=run_job, args=(state, job), daemon=True).start()
            self._json({"jobId": job.id})

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


def serve(host: str, port: int, scenarios_dir: Path, config: Path, runs_dir: Path) -> None:
    state = ServeState(scenarios_dir=scenarios_dir, config=config, runs_dir=runs_dir)
    server = make_server(state, host, port)
    bound = server.server_address[1]
    print(f"bajutsu serve → http://{host}:{bound}  (scenarios: {scenarios_dir} · Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        server.shutdown()
        server.server_close()


INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>bajutsu</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--line:#334155;--fg:#e2e8f0;--mut:#94a3b8;--acc:#38bdf8;--ok:#22c55e;--ng:#ef4444}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#0b1220;color:var(--fg)}
header{position:sticky;top:0;z-index:10;padding:.7rem 1rem;background:var(--bg);border-bottom:1px solid var(--line);font-weight:700}
header .mut{font-weight:400;color:var(--mut);font-size:.85em;margin-left:.5rem}
main{display:grid;grid-template-columns:340px 1fr;gap:1rem;padding:1rem;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem}
label{display:block;margin:.6rem 0 .2rem;color:var(--mut);font-size:.85em}
select,input[type=text]{width:100%;padding:.45rem;background:#0b1220;color:var(--fg);border:1px solid var(--line);border-radius:6px}
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
button.run{margin-top:1rem;width:100%;padding:.6rem;background:var(--acc);color:#082f49;border:0;border-radius:6px;font-weight:700;cursor:pointer}
button.run:disabled{opacity:.5;cursor:default}
.status{margin-top:.8rem;font-weight:600}.status.ok{color:var(--ok)}.status.ng{color:var(--ng)}.status.run{color:var(--acc)}
pre.out{margin:.6rem 0 0;max-height:220px;overflow:auto;background:#0b1220;border:1px solid var(--line);border-radius:6px;padding:.5rem;font-size:12px;white-space:pre-wrap}
.report{height:calc(100vh - 6rem)}iframe{width:100%;height:100%;border:1px solid var(--line);border-radius:10px;background:#fff}
.empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--mut)}
.names{color:var(--mut);font-size:.8em;margin-top:.2rem;min-height:1em}
.names .finfo{color:var(--fg);font-size:1.05em;margin-bottom:.25rem}
.scnlist{list-style:none;margin:.1rem 0 0;padding:0;font-size:1em;max-height:30vh;overflow:auto}
.scnlist li{padding:.12rem 0;color:var(--fg)}
.scnlist .sd{color:var(--mut)}
.left{display:flex;flex-direction:column;gap:1rem;height:calc(100vh - 6rem)}
.left>.card{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.panel{flex:1;min-height:0;overflow-y:auto}
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
</style></head>
<body>
<header>bajutsu <span class="mut">run a scenario · view its report (Tier 1 — not the CI gate)</span></header>
<main>
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
        <button class="run" id="go">Run</button>
        <div class="status" id="status"></div>
        <pre class="out" id="out" hidden></pre>
      </div>
      <div class="panel" id="panel-history" hidden>
        <div class="hhead"><span>Past runs</span><button class="refresh" id="refresh" title="refresh">&#8635;</button></div>
        <ul class="history" id="history"></ul>
      </div>
    </div>
  </div>
  <div class="report" id="report"><div class="empty">Run a scenario to see its report here.</div></div>
</main>
<script>
const $=s=>document.querySelector(s);
let poll=null,selectedRun=null,scnFiles=[];
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function load(){
  scnFiles=await (await fetch('/api/scenarios')).json();
  const app=await (await fetch('/api/apps')).json();
  $('#scn').innerHTML=scnFiles.map(s=>`<option value="${esc(s.path)}">${esc(s.file)}</option>`).join('');
  $('#app').innerHTML=app.map(a=>`<option>${esc(a)}</option>`).join('');
  showInfo();
}
function showInfo(){
  const f=scnFiles.find(s=>s.path===$('#scn').value),el=$('#names');
  if(!f){el.innerHTML='';return}
  let h='';
  if(f.description)h+=`<div class="finfo">${esc(f.description)}</div>`;
  if(f.scenarios&&f.scenarios.length)h+='<ul class="scnlist">'+f.scenarios.map(s=>`<li><b>${esc(s.name)}</b>${s.description?' &mdash; <span class="sd">'+esc(s.description)+'</span>':''}</li>`).join('')+'</ul>';
  el.innerHTML=h;
}
$('#scn').addEventListener('change',showInfo);
function simRow(s){
  return `<label><input type="checkbox" class="simck" value="${esc(s.udid)}"><span class="dot ${s.booted?'ok':'off'}" title="${s.booted?'booted':'shut down'}"></span><span>${esc(s.name)}</span><span class="rt">${esc(s.runtime)}${s.booted?'':' · off'}</span></label>`;
}
async function loadSims(){
  let sims=[];try{sims=await (await fetch('/api/simulators')).json()}catch(e){}
  const el=$('#sims');
  if(!sims.length){el.innerHTML='<div class="empty">no simulators found</div>';return}
  el.innerHTML=sims.map(simRow).join('');
  el.querySelectorAll('.simck').forEach(c=>c.addEventListener('change',onSimChange));
}
function pickedUdids(){return [...$('#sims').querySelectorAll('.simck:checked')].map(c=>c.value)}
function onSimChange(){const n=pickedUdids().length;if(n>0)$('#workers').value=n}
$('#simrefresh').addEventListener('click',loadSims);
$('#go').addEventListener('click',async()=>{
  if(poll)clearInterval(poll);
  $('#go').disabled=true;$('#out').hidden=false;$('#out').textContent='';
  setStatus('starting…','run');
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    scenario:$('#scn').value,app:$('#app').value,backend:$('#backend').value.trim(),udid:pickedUdids().join(',')||'booted',
    workers:parseInt($('#workers').value,10)||1,
    erase:$('#erasedev').checked||undefined,dismissAlerts:$('#nodismiss').checked?false:undefined})});
  const {jobId,error}=await r.json();
  if(error){setStatus(error,'ng');$('#go').disabled=false;return}
  poll=setInterval(()=>check(jobId),1000);check(jobId);
});
async function check(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#out').textContent=(j.lines||[]).join('\\n');$('#out').scrollTop=$('#out').scrollHeight;
  if(j.status==='running'){setStatus('running…','run');return}
  clearInterval(poll);poll=null;$('#go').disabled=false;
  setStatus(j.ok?'PASS':'FAIL', j.ok?'ok':'ng');
  if(j.runId)setReport(j.runId);
  loadHistory();
}
function setReport(id){selectedRun=id;$('#report').innerHTML=`<iframe src="/runs/${id}/report.html"></iframe>`}
async function loadHistory(){
  let runs;try{runs=await (await fetch('/api/runs')).json()}catch(e){return}
  const tab=$('#histtab');if(tab)tab.textContent='History'+(runs.length?` (${runs.length})`:'');
  const ul=$('#history');
  if(!runs.length){ul.innerHTML='<li class="muted">no runs yet</li>';return}
  ul.innerHTML=runs.map(r=>`<li data-id="${r.id}"${r.id===selectedRun?' class="sel"':''}><span class="dot ${r.ok?'ok':'ng'}"></span><span class="hid">${r.id}</span><span class="hsum">${r.passed}/${r.total}${r.scenarios.length?' · '+r.scenarios.join(', '):''}</span></li>`).join('');
  ul.querySelectorAll('li[data-id]').forEach(li=>li.addEventListener('click',()=>{setReport(li.dataset.id);ul.querySelectorAll('li').forEach(x=>x.classList.remove('sel'));li.classList.add('sel')}));
}
$('#refresh').addEventListener('click',loadHistory);
function showTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  $('#panel-run').hidden=name!=='run';$('#panel-history').hidden=name!=='history';
  if(name==='history')loadHistory();
}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.tab)));
function setStatus(t,c){const s=$('#status');s.textContent=t;s.className='status '+c}
load();
loadSims();
loadHistory();
setInterval(loadHistory,4000);
</script>
</body></html>"""

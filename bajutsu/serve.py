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

from bajutsu.config import load_config
from bajutsu.scenario import load_scenarios

# The run command prints "PASS/FAIL  runs/<id>/manifest.json"; pull <id> from it.
_RUN_ID_RE = re.compile(r"runs/([0-9A-Za-z._-]+)/manifest\.json")

Popen = Callable[..., Any]


# --- pure helpers (unit-tested without a server) ---


def list_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    """Every `*.yaml` under `scenarios_dir`, with the scenario names each file defines and a
    path (relative to cwd) the run command can take."""
    out: list[dict[str, Any]] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        try:
            names = [s.name for s in load_scenarios(path.read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            names = []
        out.append({"file": path.name, "path": str(path), "names": names})
    return out


def list_apps(config_path: Path) -> list[str]:
    try:
        return sorted(load_config(config_path.read_text(encoding="utf-8")).apps)
    except (OSError, ValueError):
        return []


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


def run_command(
    scenario: str, app: str, *, backend: str = "", udid: str = "", erase: bool = False,
    dismiss_alerts: bool = False, config: str = "bajutsu.config.yaml",
) -> list[str]:
    """The `python -m bajutsu run ...` argv for a launch request (defaults to `--no-erase`,
    reusing the device state the UI is iterating against)."""
    cmd = [sys.executable, "-m", "bajutsu", "run", scenario, "--app", app, "--config", config]
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    cmd += ["--erase"] if erase else ["--no-erase"]
    if dismiss_alerts:
        cmd += ["--dismiss-alerts"]
    return cmd


# --- jobs ---


@dataclass
class Job:
    id: str
    cmd: list[str]
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
    jobs: dict[str, Job] = field(default_factory=dict)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def new_job(self, cmd: list[str]) -> Job:
        with self._lock:
            self._seq += 1
            job = Job(id=str(self._seq), cmd=cmd)
            self.jobs[job.id] = job
        return job


def _spawn_env() -> dict[str, str]:
    """Ensure the venv bin dir (where the `idb` client lives) is on PATH for the run."""
    env = dict(os.environ)
    bindir = str(Path(sys.executable).parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def run_job(state: ServeState, job: Job) -> None:
    """Run `job.cmd`, capturing combined output line-by-line and the produced run id."""
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
            cmd = run_command(
                body["scenario"], body["app"],
                backend=body.get("backend", ""), udid=body.get("udid", ""),
                erase=bool(body.get("erase", False)), dismiss_alerts=bool(body.get("dismissAlerts", False)),
                config=str(state.config),
            )
            job = state.new_job(cmd)
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
header{padding:.7rem 1rem;background:var(--bg);border-bottom:1px solid var(--line);font-weight:700}
header .mut{font-weight:400;color:var(--mut);font-size:.85em;margin-left:.5rem}
main{display:grid;grid-template-columns:340px 1fr;gap:1rem;padding:1rem;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem}
label{display:block;margin:.6rem 0 .2rem;color:var(--mut);font-size:.85em}
select,input[type=text]{width:100%;padding:.45rem;background:#0b1220;color:var(--fg);border:1px solid var(--line);border-radius:6px}
.row{display:flex;gap:1rem}.row>div{flex:1}
.checks{display:flex;gap:1rem;margin-top:.6rem;align-items:center}.checks label{display:flex;gap:.35rem;align-items:center;margin:0;color:var(--fg)}
button.run{margin-top:1rem;width:100%;padding:.6rem;background:var(--acc);color:#082f49;border:0;border-radius:6px;font-weight:700;cursor:pointer}
button.run:disabled{opacity:.5;cursor:default}
.status{margin-top:.8rem;font-weight:600}.status.ok{color:var(--ok)}.status.ng{color:var(--ng)}.status.run{color:var(--acc)}
pre.out{margin:.6rem 0 0;max-height:220px;overflow:auto;background:#0b1220;border:1px solid var(--line);border-radius:6px;padding:.5rem;font-size:12px;white-space:pre-wrap}
.report{height:calc(100vh - 6rem)}iframe{width:100%;height:100%;border:1px solid var(--line);border-radius:10px;background:#fff}
.empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--mut)}
.names{color:var(--mut);font-size:.8em;margin-top:.2rem;min-height:1em}
.left{display:flex;flex-direction:column;gap:1rem}
.hhead{display:flex;justify-content:space-between;align-items:center;font-weight:600;margin-bottom:.5rem}
.refresh{background:none;border:1px solid var(--line);color:var(--mut);border-radius:6px;cursor:pointer;padding:.1rem .45rem}
.history{list-style:none;margin:0;padding:0;max-height:42vh;overflow:auto}
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
      <label>Scenario</label>
      <select id="scn"></select><div class="names" id="names"></div>
      <label>App</label><select id="app"></select>
      <div class="row"><div><label>Backend</label><input id="backend" type="text" placeholder="idb"></div>
        <div><label>UDID</label><input id="udid" type="text" placeholder="booted"></div></div>
      <div class="checks">
        <label><input type="checkbox" id="noerase" checked> no-erase</label>
        <label><input type="checkbox" id="dismiss"> dismiss-alerts</label>
      </div>
      <button class="run" id="go">Run</button>
      <div class="status" id="status"></div>
      <pre class="out" id="out" hidden></pre>
    </div>
    <div class="card">
      <div class="hhead"><span>History</span><button class="refresh" id="refresh" title="refresh">&#8635;</button></div>
      <ul class="history" id="history"></ul>
    </div>
  </div>
  <div class="report" id="report"><div class="empty">Run a scenario to see its report here.</div></div>
</main>
<script>
const $=s=>document.querySelector(s);
let poll=null;
async function load(){
  const scn=await (await fetch('/api/scenarios')).json();
  const app=await (await fetch('/api/apps')).json();
  $('#scn').innerHTML=scn.map(s=>`<option value="${s.path}" data-names="${(s.names||[]).join(', ')}">${s.file}</option>`).join('');
  $('#app').innerHTML=app.map(a=>`<option>${a}</option>`).join('');
  showNames();
  loadHistory();
}
function showNames(){const o=$('#scn').selectedOptions[0];$('#names').textContent=o?o.dataset.names:''}
$('#scn').addEventListener('change',showNames);
$('#go').addEventListener('click',async()=>{
  if(poll)clearInterval(poll);
  $('#go').disabled=true;$('#out').hidden=false;$('#out').textContent='';
  setStatus('starting…','run');
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    scenario:$('#scn').value,app:$('#app').value,backend:$('#backend').value.trim(),udid:$('#udid').value.trim(),
    erase:!$('#noerase').checked,dismissAlerts:$('#dismiss').checked})});
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
  loadHistory(j.runId);
}
function setReport(id){$('#report').innerHTML=`<iframe src="/runs/${id}/report.html"></iframe>`}
function select(li){$('#history').querySelectorAll('li').forEach(x=>x.classList.remove('sel'));li.classList.add('sel')}
async function loadHistory(sel){
  const runs=await (await fetch('/api/runs')).json();
  const ul=$('#history');
  if(!runs.length){ul.innerHTML='<li class="muted">no runs yet</li>';return}
  ul.innerHTML=runs.map(r=>`<li data-id="${r.id}"><span class="dot ${r.ok?'ok':'ng'}"></span><span class="hid">${r.id}</span><span class="hsum">${r.passed}/${r.total}${r.scenarios.length?' · '+r.scenarios.join(', '):''}</span></li>`).join('');
  ul.querySelectorAll('li[data-id]').forEach(li=>li.addEventListener('click',()=>{setReport(li.dataset.id);select(li)}));
  if(sel){const m=ul.querySelector('li[data-id="'+sel+'"]');if(m)select(m)}
}
$('#refresh').addEventListener('click',()=>loadHistory());
function setStatus(t,c){const s=$('#status');s.textContent=t;s.className='status '+c}
load();
</script>
</body></html>"""

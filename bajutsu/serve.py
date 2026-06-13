"""`bajutsu serve` — a local web UI to author scenarios and run them.

A Tier-1 convenience (authoring / operation), **never part of the CI gate**. Two top-level
tabs over the CLI: **Record** authors a scenario from a natural-language goal (`python -m
bajutsu record …`), streaming the agent's turn-by-turn progress and writing the result under
the scenarios dir; **Replay** runs a scenario (`python -m bajutsu run …`) and shows its
self-contained `report.html`. Each request spawns the CLI on a background thread, streams its
output, and the produced `runs/<id>/` tree is served so the report's relative asset links
resolve. Stdlib only — the same `ThreadingHTTPServer` approach as the network collector
([[network]]).
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from bajutsu import env
from bajutsu.config import load_config, resolve
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


def app_build_info(config_path: Path, app: str) -> tuple[str | None, str | None]:
    """`(app_path, build)` for `app` from config — the built `.app` path and the shell command
    that builds it. Either may be None (unset or any load/resolve error); the run then proceeds
    without an on-demand build (and the runner reports a missing binary as before)."""
    try:
        eff = resolve(load_config(config_path.read_text(encoding="utf-8")), app)
    except (OSError, ValueError, KeyError):
        return (None, None)
    return (eff.app_path, eff.build)


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
    cmd = [sys.executable, "-m", "bajutsu", "run", scenario, "--app", app, "--config", config,
           "--progress"]  # stream per-scenario/step progress into the run log
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


def record_command(
    out: str, app: str, goal: str, *, agent: str = "", backend: str = "", udid: str = "",
    erase: bool | None = None, dismiss_alerts: bool | None = None,
    config: str = "bajutsu.config.yaml",
) -> list[str]:
    """The `python -m bajutsu record OUT --app … --goal …` argv for an authoring request — the
    Tier-1 record loop the Record tab drives. `agent` picks the brain ("api" / "claude-code");
    `erase` / `dismiss_alerts` mirror `run_command` (None leaves the CLI default — record erases
    and dismisses by default), and `out` is the `*.yaml` the recorded scenario is written to."""
    cmd = [sys.executable, "-m", "bajutsu", "record", out, "--app", app, "--goal", goal,
           "--config", config]
    if agent:
        cmd += ["--agent", agent]
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    if erase is True:
        cmd += ["--erase"]
    elif erase is False:
        cmd += ["--no-erase"]
    if dismiss_alerts is True:
        cmd += ["--dismiss-alerts"]
    elif dismiss_alerts is False:
        cmd += ["--no-dismiss-alerts"]
    return cmd


def scenario_out_path(scenarios_dir: Path, name: str) -> Path:
    """A safe `*.yaml` path under `scenarios_dir` for an authored scenario. `name` is the user's
    file name (or, lacking one, the goal); path separators and control chars are stripped so a
    request can never escape the scenarios dir, and a blank / unusable name falls back to
    'authored'. A `.yaml` suffix is normalized so 'foo' and 'foo.yaml' name the same file."""
    stem = (name or "").strip().replace("/", "-").replace("\\", "-")
    if stem.endswith(".yaml"):
        stem = stem[:-len(".yaml")]
    stem = re.sub(r"[\x00-\x1f]", "", stem).strip(" .")
    if not stem or stem in {".", ".."}:
        stem = "authored"
    return scenarios_dir / f"{stem}.yaml"


def unique_scenario_path(path: Path, stamp: str | None = None) -> Path:
    """`path` if it's free, else the same stem with the run's date-time appended
    (`foo` → `foo-20260613-153045`) so authoring a scenario never overwrites an existing one."""
    if not path.exists():
        return path
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.parent / f"{path.stem}-{stamp}.yaml"


def _scenario_path(scenarios_dir: Path, p: str | None) -> Path | None:
    """Resolve `p` (the path the UI passes for a scenario to read or save) to a `*.yaml` file
    inside `scenarios_dir`, or None if it would escape the dir or isn't a scenario file. The
    file need not exist yet (saving a freshly authored scenario), but its parent must be the
    scenarios dir."""
    if not p:
        return None
    target = Path(p)
    if not target.is_absolute():
        target = scenarios_dir / target
    target = target.resolve()
    base = scenarios_dir.resolve()
    if target != base and base not in target.parents:
        return None
    if target.suffix != ".yaml":
        return None
    return target


# --- jobs ---


@dataclass
class Job:
    id: str
    cmd: list[str]
    udids: list[str] = field(default_factory=list)  # devices to boot before the run
    app_path: str | None = None  # built .app the run needs; built on demand if missing
    build: str | None = None     # shell command that builds app_path (None = no on-demand build)
    status: str = "running"      # running | done
    exit_code: int | None = None
    run_id: str | None = None    # the runs/<id> a `run` job produced, parsed from its output
    out_path: str | None = None  # the scenario a `record` job authored (so the UI can load it)
    cancelled: bool = False      # a /cancel request stopped this job (vs. a real pass/fail)
    proc: Any = None             # the live subprocess (build or run), so a cancel can terminate it
    lines: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def view(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id, "status": self.status, "exitCode": self.exit_code,
                "runId": self.run_id, "outPath": self.out_path, "cancelled": self.cancelled,
                "ok": (self.exit_code == 0 and not self.cancelled) if self.status == "done" else None,
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

    def new_job(
        self, cmd: list[str], udids: list[str] | None = None,
        app_path: str | None = None, build: str | None = None, out_path: str | None = None,
    ) -> Job:
        with self._lock:
            self._seq += 1
            job = Job(id=str(self._seq), cmd=cmd, udids=list(udids or []),
                      app_path=app_path, build=build, out_path=out_path)
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


def _terminate(proc: Any) -> None:
    """Best-effort stop of a live subprocess; ignore an already-exited / fake proc."""
    try:
        proc.terminate()
    except (OSError, ProcessLookupError, AttributeError):
        pass


def _register_proc(job: Job, proc: Any) -> bool:
    """Attach `proc` as the job's live subprocess so a cancel request can reach it. If a cancel
    already arrived, kill `proc` at once and return False so the caller stops before streaming."""
    with job.lock:
        if job.cancelled:
            kill = True
        else:
            job.proc = proc
            kill = False
    if kill:
        _terminate(proc)
    return not kill


def cancel_job(job: Job) -> bool:
    """Request cancellation of a running job: flag it and terminate its current subprocess (the
    streamed output then ends and run_job marks the job done). Returns False if already finished."""
    with job.lock:
        if job.status == "done":
            return False
        job.cancelled = True
        proc = job.proc
        if not job.lines or job.lines[-1] != "cancelled":
            job.lines.append("cancelled")
    if proc is not None:
        _terminate(proc)
    return True


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


def _build_app(state: ServeState, job: Job) -> bool:
    """Build the app's binary on demand when it is missing. Returns True if the run may proceed:
    nothing to build (no `build` command, no `app_path`, or the binary already exists), or the
    build command succeeded. Returns False (marking the job failed) only when a needed build
    fails — so the run isn't spawned against a missing binary."""
    if not job.build or not job.app_path:
        return True
    if (state.cwd / job.app_path).exists():
        return True
    _log(job, f"app binary missing ({job.app_path}) — building: {job.build}")
    try:
        proc = state.popen(
            job.build, cwd=str(state.cwd), env=_spawn_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, shell=True,
        )
        if not _register_proc(job, proc):
            proc.wait()
            with job.lock:
                job.exit_code, job.status, job.proc = proc.returncode or 1, "done", None
            return False
        for raw in proc.stdout or []:
            _log(job, raw.rstrip("\n"))
        proc.wait()
        code = proc.returncode
    except OSError as e:
        _log(job, f"build failed: {e}")
        code = 1
    if code != 0:
        _log(job, f"build failed (exit {code}) — skipping the run")
        with job.lock:
            job.exit_code = code
            job.status = "done"
        return False
    _log(job, "build ok")
    return True


def run_job(state: ServeState, job: Job) -> None:
    """Boot the job's devices (if any), build the app if its binary is missing, then run
    `job.cmd`, capturing combined output line-by-line and the produced run id."""
    if not _boot_devices(state, job):
        return
    if not _build_app(state, job):
        return
    proc = state.popen(
        job.cmd, cwd=str(state.cwd), env=_spawn_env(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    if not _register_proc(job, proc):
        proc.wait()
        with job.lock:
            job.exit_code, job.status, job.proc = proc.returncode or 1, "done", None
        return
    for raw in proc.stdout or []:
        line = raw.rstrip("\n")
        match = _RUN_ID_RE.search(line)
        with job.lock:
            job.lines.append(line)
            if match:
                job.run_id = match.group(1)
    proc.wait()
    with job.lock:
        job.proc = None
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
            elif path == "/api/scenario":
                qs = parse_qs(urlparse(self.path).query)
                target = _scenario_path(state.scenarios_dir, next(iter(qs.get("path") or []), None))
                if target is None or not target.is_file():
                    self._json({"error": "not found"}, 404)
                else:
                    self._json({"yaml": target.read_text(encoding="utf-8")})
            elif path.startswith("/api/jobs/"):
                job = state.jobs.get(path[len("/api/jobs/"):])
                self._json(job.view() if job else {"error": "no such job"}, 200 if job else 404)
            elif path.startswith("/runs/"):
                self._serve_run_file(unquote(path[len("/runs/"):]))
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            if path == "/api/run":
                self._post_run(body)
            elif path == "/api/record":
                self._post_record(body)
            elif path == "/api/scenario":
                self._post_scenario(body)
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job = state.jobs.get(path[len("/api/jobs/"):-len("/cancel")])
                if job is None:
                    self._json({"error": "no such job"}, 404)
                else:
                    self._json({"cancelled": cancel_job(job)})
            else:
                self._json({"error": "not found"}, 404)

        # Concrete picked devices are booted (and waited on) before a run/record; the "booted"
        # alias names whatever is already up, so it is not a boot target.
        @staticmethod
        def _boot_targets(udid: str) -> list[str]:
            return [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]

        def _post_run(self, body: dict[str, Any]) -> None:
            if not body.get("scenario") or not body.get("app"):
                self._json({"error": "scenario and app are required"}, 400)
                return
            udid = str(body.get("udid", "") or "")
            cmd = run_command(
                body["scenario"], body["app"],
                backend=body.get("backend", ""), udid=udid,
                workers=_int(body.get("workers"), 1),
                erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
                dismiss_alerts=body["dismissAlerts"] if isinstance(body.get("dismissAlerts"), bool) else None,
                config=str(state.config),
            )
            app_path, build = app_build_info(state.config, body["app"])
            job = state.new_job(cmd, udids=self._boot_targets(udid), app_path=app_path, build=build)
            threading.Thread(target=run_job, args=(state, job), daemon=True).start()
            self._json({"jobId": job.id})

        def _post_record(self, body: dict[str, Any]) -> None:
            """Author a scenario from a natural-language goal (the Record tab). Spawns the same
            `record` loop the CLI runs, on a background thread, writing the recorded scenario to a
            `*.yaml` under the scenarios dir — so it then shows up in the Replay tab's list."""
            if not body.get("goal") or not body.get("app"):
                self._json({"error": "goal and app are required"}, 400)
                return
            out = unique_scenario_path(
                scenario_out_path(state.scenarios_dir, str(body.get("name") or "generated"))
            )
            udid = str(body.get("udid", "") or "")
            cmd = record_command(
                str(out), body["app"], str(body["goal"]),
                agent=body.get("agent", ""), backend=body.get("backend", ""), udid=udid,
                erase=body["erase"] if isinstance(body.get("erase"), bool) else None,
                dismiss_alerts=body["dismissAlerts"] if isinstance(body.get("dismissAlerts"), bool) else None,
                config=str(state.config),
            )
            app_path, build = app_build_info(state.config, body["app"])
            job = state.new_job(cmd, udids=self._boot_targets(udid), app_path=app_path,
                                build=build, out_path=str(out))
            threading.Thread(target=run_job, args=(state, job), daemon=True).start()
            self._json({"jobId": job.id, "path": str(out)})

        def _post_scenario(self, body: dict[str, Any]) -> None:
            """Save an edited scenario back to its `*.yaml` (the Record tab's editor / the
            edit-and-re-run loop). The YAML is validated first so a malformed save is rejected
            with its parse error rather than corrupting the scenarios dir."""
            target = _scenario_path(state.scenarios_dir, body.get("path"))
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
</style></head>
<body>
<header>
  <span class="brand">bajutsu</span>
  <span class="mut">natural-language authoring · deterministic replay (Tier 1 — not the CI gate)</span>
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
        <input id="rec-name" type="text" placeholder="generated.yaml — leave blank to default">
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
      <button class="run" id="rec-go">Generate scenario</button>
      <div class="status" id="rec-status"></div>
      <pre class="out" id="rec-out" data-empty="Enter a goal and press Generate to watch the agent author it, turn by turn."></pre>
    </div>
    <div class="card yamlpanel" id="rec-yamlpanel">
      <div class="yamlhead">
        <span class="ttl">Generated scenario</span>
        <span class="muted" id="rec-yamlinfo" style="font-size:.8em"></span>
        <button class="savebtn" id="rec-save" disabled>Save</button>
      </div>
      <textarea id="rec-yaml" class="yaml" placeholder="The recorded scenario YAML appears here once authoring finishes — edit and Save, then run it in the Replay tab."></textarea>
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
    <button class="run" id="go">Run</button>
    <div class="status" id="status"></div>
    <pre class="out" id="out" data-empty="Run a scenario to see its output here."></pre>
  </div>
  <div class="report" id="report"><div class="empty">Run a scenario to see its report here.</div></div>
</main>
<script>
const $=s=>document.querySelector(s);
let poll=null,recPoll=null,selectedRun=null,recPath=null,scnFiles=[],apps=[],sims=[];
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function setStatus(el,t,c){el.textContent=t;el.className='status '+c}

// ---- top-level Record / Replay views ----
function showView(name){
  document.querySelectorAll('.toptab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));
  $('#view-record').hidden=name!=='record';$('#view-replay').hidden=name!=='replay';
  if(name==='replay')loadHistory();
}
document.querySelectorAll('.toptab').forEach(t=>t.addEventListener('click',()=>showView(t.dataset.view)));

// ---- shared data: apps, scenarios, simulators (used by both views) ----
async function loadShared(){
  try{apps=await (await fetch('/api/apps')).json()}catch(e){apps=[]}
  const opts=apps.map(a=>`<option>${esc(a)}</option>`).join('');
  $('#app').innerHTML=opts;$('#rec-app').innerHTML=opts;
  await loadScenarios();
}
async function loadScenarios(){
  try{scnFiles=await (await fetch('/api/scenarios')).json()}catch(e){scnFiles=[]}
  $('#scn').innerHTML=scnFiles.map(s=>`<option value="${esc(s.path)}">${esc(s.file)}</option>`).join('');
  showInfo();
}
async function loadSims(){
  try{sims=await (await fetch('/api/simulators')).json()}catch(e){sims=[]}
  // Replay: multi-select checkboxes (parallel pool).
  const el=$('#sims');
  el.innerHTML=sims.length?sims.map(s=>`<label><input type="checkbox" class="simck" value="${esc(s.udid)}"><span class="dot ${s.booted?'ok':'off'}" title="${s.booted?'booted':'shut down'}"></span><span>${esc(s.name)}</span><span class="rt">${esc(s.runtime)}${s.booted?'':' · off'}</span></label>`).join(''):'<div class="empty">no simulators found</div>';
  el.querySelectorAll('.simck').forEach(c=>c.addEventListener('change',onSimChange));
  // Record: single-device dropdown ("booted" = whatever is already up).
  $('#rec-device').innerHTML='<option value="booted">booted (already up)</option>'+sims.map(s=>`<option value="${esc(s.udid)}">${esc(s.name)} · ${esc(s.runtime)}${s.booted?'':' · off'}</option>`).join('');
}

// ---- Record: author a scenario from a goal ----
$('#rec-simrefresh').addEventListener('click',loadSims);
$('#rec-go').addEventListener('click',async()=>{
  const goal=$('#rec-goal').value.trim();
  if(!goal){setStatus($('#rec-status'),'enter a goal first','ng');return}
  if(recPoll)clearInterval(recPoll);
  $('#rec-go').disabled=true;$('#rec-go').textContent='Authoring…';$('#rec-out').textContent='';
  $('#rec-yaml').value='';$('#rec-save').disabled=true;$('#rec-yamlinfo').textContent='';recPath=null;
  setStatus($('#rec-status'),'','run');
  const r=await fetch('/api/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    goal,app:$('#rec-app').value,agent:$('#rec-agent').value,backend:$('#rec-backend').value.trim(),
    udid:$('#rec-device').value||'booted',name:$('#rec-name').value.trim()||undefined,
    erase:$('#rec-erase').checked,dismissAlerts:$('#rec-nodismiss').checked?false:undefined})});
  const {jobId,path,error}=await r.json();
  if(error){setStatus($('#rec-status'),error,'ng');$('#rec-go').disabled=false;$('#rec-go').textContent='Generate scenario';return}
  recPath=path;
  recPoll=setInterval(()=>recCheck(jobId),1000);recCheck(jobId);
});
async function recCheck(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#rec-out').textContent=(j.lines||[]).join('\\n');$('#rec-out').scrollTop=$('#rec-out').scrollHeight;
  if(j.status==='running')return;
  clearInterval(recPoll);recPoll=null;$('#rec-go').disabled=false;$('#rec-go').textContent='Generate scenario';
  setStatus($('#rec-status'),j.ok?'authored ✓':'failed', j.ok?'ok':'ng');
  if(j.ok&&(j.outPath||recPath)){await loadGenerated(j.outPath||recPath);loadScenarios();}
}
async function loadGenerated(path){
  recPath=path;
  try{
    const d=await (await fetch('/api/scenario?path='+encodeURIComponent(path))).json();
    if(d.yaml!=null){$('#rec-yaml').value=d.yaml;$('#rec-save').disabled=false;
      $('#rec-yamlinfo').textContent=path.split('/').pop();}
  }catch(e){}
}
$('#rec-save').addEventListener('click',async()=>{
  if(!recPath)return;
  $('#rec-save').disabled=true;$('#rec-save').textContent='Saving…';
  const r=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:recPath,yaml:$('#rec-yaml').value})});
  const d=await r.json();
  $('#rec-save').textContent='Save';$('#rec-save').disabled=false;
  if(d.error){setStatus($('#rec-status'),d.error,'ng')}
  else{setStatus($('#rec-status'),'saved ✓','ok');loadScenarios()}
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
  $('#go').disabled=true;$('#go').textContent='Running…';$('#out').textContent='';
  setStatus($('#status'),'','run');
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    scenario:$('#scn').value,app:$('#app').value,backend:$('#backend').value.trim(),udid:pickedUdids().join(',')||'booted',
    workers:parseInt($('#workers').value,10)||1,
    erase:$('#erasedev').checked||undefined,dismissAlerts:$('#nodismiss').checked?false:undefined})});
  const {jobId,error}=await r.json();
  if(error){setStatus($('#status'),error,'ng');$('#go').disabled=false;$('#go').textContent='Run';return}
  poll=setInterval(()=>check(jobId),1000);check(jobId);
});
async function check(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#out').textContent=(j.lines||[]).join('\\n');$('#out').scrollTop=$('#out').scrollHeight;
  if(j.status==='running')return;  // the Run button (disabled, "Running…") shows the running state
  clearInterval(poll);poll=null;$('#go').disabled=false;$('#go').textContent='Run';
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
  ul.innerHTML=runs.map(r=>`<li data-id="${r.id}"${r.id===selectedRun?' class="sel"':''}><span class="dot ${r.ok?'ok':'ng'}"></span><span class="hid">${r.id}</span><span class="hsum">${r.passed}/${r.total}${r.scenarios.length?' · '+r.scenarios.join(', '):''}</span></li>`).join('');
  ul.querySelectorAll('li[data-id]').forEach(li=>li.addEventListener('click',()=>{setReport(li.dataset.id);ul.querySelectorAll('li').forEach(x=>x.classList.remove('sel'));li.classList.add('sel')}));
}
$('#refresh').addEventListener('click',loadHistory);
function showTab(name){
  document.querySelectorAll('#view-replay .tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  $('#panel-run').hidden=name!=='run';$('#panel-history').hidden=name!=='history';
  if(name==='history')loadHistory();
}
document.querySelectorAll('#view-replay .tab').forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.tab)));

loadShared();
loadSims();
loadHistory();
setInterval(loadHistory,4000);
</script>
</body></html>"""

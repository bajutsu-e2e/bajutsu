"""Reporting — manifest.json (the single source of truth), JUnit XML, and a
self-contained interactive HTML report.

A run executes one or more scenarios (list[RunResult]). The manifest records the
step/expect outcomes per scenario; JUnit feeds CI. report.html embeds the screen
recording and the captured logs (device log / app trace) with no external assets:
inline CSS + a little vanilla JS for collapsing, tab switching, and log filtering.
"""

from __future__ import annotations

import html as _html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from bajutsu.evidence import Artifact
from bajutsu.orchestrator import RunResult

# How many trailing log lines to embed inline (the full file is always linked).
_LOG_MAX_LINES = 2000


def manifest_dict(run_id: str, results: list[RunResult]) -> dict[str, object]:
    """Build the manifest. RunResult and its parts are dataclasses, so asdict()
    captures step/expect outcomes verbatim."""
    return {
        "runId": run_id,
        "ok": all(r.ok for r in results),
        "scenarios": [asdict(r) for r in results],
    }


def _details(r: RunResult) -> str:
    lines: list[str] = []
    for s in r.steps:
        status = "ok" if s.ok else "FAIL"
        lines.append(f"step {s.index} {s.action}: {status} {s.reason}".rstrip())
    for a in r.expect_results:
        status = "ok" if a.ok else "FAIL"
        lines.append(f"expect {a.kind}: {status} {a.reason}".rstrip())
    return "\n".join(lines)


def junit_xml(results: list[RunResult]) -> str:
    """One testcase per scenario; a failing scenario gets a <failure>."""
    failures = sum(0 if r.ok else 1 for r in results)
    suite = ET.Element(
        "testsuite",
        name="bajutsu",
        tests=str(len(results)),
        failures=str(failures),
    )
    for r in results:
        case = ET.SubElement(suite, "testcase", name=r.scenario, classname="bajutsu")
        if not r.ok:
            failure = ET.SubElement(case, "failure", message=r.failure or "failed")
            failure.text = _details(r)
    return ET.tostring(suite, encoding="unicode")


# --- HTML report ---


def _badge(ok: bool) -> str:
    return '<span class="pass">PASS</span>' if ok else '<span class="fail">FAIL</span>'


def _artifact(r: RunResult, kind: str) -> Artifact | None:
    return next((a for a in r.artifacts if a.kind == kind), None)


def _read_lines(run_dir: Path, name: str, max_lines: int) -> tuple[list[str] | None, int]:
    try:
        text = (run_dir / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, 0
    lines = text.splitlines()
    total = len(lines)
    return (lines[-max_lines:] if total > max_lines else lines), total


def _read_json(run_dir: Path, name: str) -> Any:
    try:
        return json.loads((run_dir / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _steps_panel(r: RunResult, e: Any) -> str:
    rows = [
        f"<tr class='{'ok' if s.ok else 'ng'}'><td>{s.index}</td><td>{e(s.action)}</td>"
        f"<td>{'ok' if s.ok else 'FAIL'}</td><td>{s.duration_s:.3f}s</td><td>{e(s.reason)}</td></tr>"
        for s in r.steps
    ]
    rows += [
        f"<tr class='{'ok' if a.ok else 'ng'}'><td>expect</td><td>{e(a.kind)}</td>"
        f"<td>{'ok' if a.ok else 'FAIL'}</td><td></td><td>{e(a.reason)}</td></tr>"
        for a in r.expect_results
    ]
    return (
        "<table><thead><tr><th>#</th><th>action</th><th>result</th>"
        "<th>time</th><th>reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _log_panel(run_dir: Path | None, art: Artifact, e: Any) -> str:
    link = f'<a href="{e(art.name)}">{e(art.name)}</a>'
    lines, total = _read_lines(run_dir, art.name, _LOG_MAX_LINES) if run_dir else (None, 0)
    if lines is None:
        return f'<div class="muted">device log: {link}</div>'
    shown = len(lines)
    note = f"showing last {shown} of {total} lines · " if total > shown else ""
    body = "".join(f'<div class="ln">{e(line)}</div>' for line in lines) or '<div class="ln muted">(empty)</div>'
    return (
        '<div class="logbar"><input class="logfilter" type="search" placeholder="filter log…">'
        f'<span class="logcount muted">{shown} lines</span></div>'
        f'<div class="muted">{note}{link}</div>'
        f'<div class="log">{body}</div>'
    )


def _trace_panel(run_dir: Path | None, art: Artifact, e: Any) -> str:
    link = f'<a href="{e(art.name)}">{e(art.name)}</a>'
    data = _read_json(run_dir, art.name) if run_dir else None
    if not isinstance(data, list) or not data:
        return f'<div class="muted">app trace: {link} (no intervals)</div>'
    rows = "".join(
        f"<tr><td>{e(str(d.get('name', '')))}</td><td>{e(str(d.get('durationMs', '')))} ms</td>"
        f"<td>{e(str(d.get('begin', '')))}</td><td>{e(str(d.get('end', '')))}</td></tr>"
        for d in data
        if isinstance(d, dict)
    )
    return (
        "<table><thead><tr><th>interval</th><th>duration</th><th>begin</th><th>end</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _scenario_section(i: int, r: RunResult, run_dir: Path | None, e: Any) -> str:
    video = _artifact(r, "video")
    media = (
        f'<video controls preload="metadata" src="{e(video.name)}"></video>'
        if video else '<div class="muted">no recording</div>'
    )
    panels: list[tuple[str, str, str]] = [("steps", "Steps", _steps_panel(r, e))]
    dev = _artifact(r, "deviceLog")
    if dev is not None:
        panels.append(("log", "Device Log", _log_panel(run_dir, dev, e)))
    trace = _artifact(r, "appTrace")
    if trace is not None:
        panels.append(("trace", "App Trace", _trace_panel(run_dir, trace, e)))

    first = panels[0][0]
    tabs = "".join(
        f'<button class="tab{" active" if key == first else ""}" data-tab="{key}">{e(label)}</button>'
        for key, label, _ in panels
    )
    bodies = "".join(
        f'<div class="panel{" active" if key == first else ""}" data-panel="{key}">{html}</div>'
        for key, _, html in panels
    )
    open_attr = "" if r.ok else " open"
    return (
        f'<details class="scn" data-ok="{str(r.ok).lower()}"{open_attr}>'
        f'<summary><span class="dot{"" if r.ok else " ng"}"></span>'
        f'<span class="sname">{e(r.scenario)}</span> {_badge(r.ok)}</summary>'
        f'<div class="body"><div class="media">{media}</div>'
        f'<div class="detail"><div class="tabs">{tabs}</div>{bodies}</div></div>'
        f"</details>"
    )


_STYLE = """
:root{--ok:#0a7d33;--ng:#c0362c;--bg:#f6f6f8;--card:#fff;--line:#e4e4e7;--mut:#777;--ink:#1c1c1e}
*{box-sizing:border-box}
body{font-family:-apple-system,system-ui,"Segoe UI",sans-serif;margin:0;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:5;background:var(--ink);color:#fff;padding:.7rem 1.1rem;
 display:flex;gap:.9rem;align-items:center;flex-wrap:wrap}
header h1{font-size:1rem;margin:0;font-weight:600}
.stats{display:flex;gap:.4rem;font-size:.82rem}
.chip{padding:.12rem .55rem;border-radius:999px;background:#3a3a3c}
.chip.ok{background:var(--ok)} .chip.ng{background:var(--ng)}
.ctl{margin-left:auto;display:flex;gap:.6rem;align-items:center;font-size:.82rem}
.ctl label{display:flex;gap:.3rem;align-items:center;cursor:pointer}
.hbtn{background:#3a3a3c;color:#fff;border:0;border-radius:6px;padding:.25rem .6rem;cursor:pointer;font:inherit;font-size:.8rem}
main{padding:1.1rem;max-width:1100px;margin:0 auto}
details.scn{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:0 0 .9rem;overflow:hidden}
details.scn[data-ok=false]{border-color:#f1c4bf}
summary{list-style:none;cursor:pointer;padding:.7rem 1rem;display:flex;gap:.55rem;align-items:center;font-weight:600}
summary::-webkit-details-marker{display:none}
.sname{flex:1}
.dot{width:.6rem;height:.6rem;border-radius:50%;background:var(--ok);flex:none} .dot.ng{background:var(--ng)}
.body{padding:0 1rem 1rem;display:grid;grid-template-columns:300px 1fr;gap:1.1rem;align-items:start}
@media(max-width:760px){.body{grid-template-columns:1fr}}
video{width:100%;border:1px solid var(--line);border-radius:8px;background:#000}
.tabs{display:flex;gap:.2rem;border-bottom:1px solid var(--line);margin-bottom:.55rem}
.tab{padding:.35rem .75rem;border:0;background:none;cursor:pointer;font:inherit;font-size:.84rem;
 color:var(--mut);border-bottom:2px solid transparent}
.tab.active{color:var(--ink);border-bottom-color:var(--ink);font-weight:600}
.panel{display:none} .panel.active{display:block}
table{border-collapse:collapse;width:100%;font-size:.84rem}
th,td{border-bottom:1px solid var(--line);padding:.32rem .5rem;text-align:left}
th{color:var(--mut);font-weight:600}
tr.ng{background:#fff4f3}
.pass{color:var(--ok);font-weight:700} .fail{color:var(--ng);font-weight:700}
.logbar{display:flex;gap:.5rem;align-items:center;margin:.1rem 0 .35rem}
.logbar input{flex:1;padding:.32rem .55rem;border:1px solid var(--line);border-radius:6px;font:inherit;font-size:.84rem}
.log{background:#1c1c1e;color:#e6e6e6;border-radius:8px;padding:.5rem .65rem;max-height:360px;overflow:auto;
 font:12px/1.55 ui-monospace,Menlo,Consolas,monospace;counter-reset:ln}
.log .ln{white-space:pre-wrap;word-break:break-word;padding-left:3.2rem;text-indent:-3.2rem}
.log .ln::before{counter-increment:ln;content:counter(ln);display:inline-block;width:2.8rem;
 margin-right:.4rem;color:#6b6b6e;text-align:right;text-indent:0}
.log .ln.hide{display:none}
.muted{color:var(--mut);font-size:.8rem}
a{color:#0a6cff}
"""

_SCRIPT = """
(function(){
  document.addEventListener('click', function(e){
    var t = e.target.closest('.tab'); if(!t) return;
    var scn = t.closest('.scn'), name = t.getAttribute('data-tab');
    scn.querySelectorAll('.tab').forEach(function(b){ b.classList.toggle('active', b===t); });
    scn.querySelectorAll('.panel').forEach(function(p){ p.classList.toggle('active', p.getAttribute('data-panel')===name); });
  });
  document.addEventListener('input', function(e){
    if(!e.target.classList.contains('logfilter')) return;
    var panel = e.target.closest('.panel'), q = e.target.value.toLowerCase(), n = 0;
    panel.querySelectorAll('.log .ln').forEach(function(l){
      var hit = !q || l.textContent.toLowerCase().indexOf(q) !== -1;
      l.classList.toggle('hide', !hit); if(hit) n++;
    });
    var cnt = panel.querySelector('.logcount'); if(cnt) cnt.textContent = n + ' lines';
  });
  window.onlyFailures = function(cb){
    document.querySelectorAll('details.scn').forEach(function(d){
      d.style.display = (cb.checked && d.getAttribute('data-ok')==='true') ? 'none' : '';
    });
  };
  window.toggleAll = function(open){
    document.querySelectorAll('details.scn').forEach(function(d){ d.open = open; });
  };
})();
"""


def html_report(run_id: str, results: list[RunResult], run_dir: Path | None = None) -> str:
    """A self-contained interactive HTML report (inline CSS + JS, no external assets).

    When `run_dir` is given the captured logs/traces are embedded inline (so the
    report works opened directly from disk); otherwise only the structure renders.
    """
    e = _html.escape
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    overall = failed == 0
    sections = "".join(_scenario_section(i, r, run_dir, e) for i, r in enumerate(results))
    header = (
        f"<header><h1>Bajutsu run {e(run_id)} {_badge(overall)}</h1>"
        f'<div class="stats"><span class="chip ok">{passed} passed</span>'
        f'<span class="chip ng">{failed} failed</span></div>'
        '<div class="ctl"><label><input type="checkbox" onchange="onlyFailures(this)"> only failures</label>'
        '<button class="hbtn" onclick="toggleAll(true)">expand all</button>'
        '<button class="hbtn" onclick="toggleAll(false)">collapse all</button></div></header>'
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Bajutsu run {e(run_id)}</title><style>{_STYLE}</style></head>"
        f"<body>{header}<main>{sections}</main><script>{_SCRIPT}</script></body></html>"
    )


def write_report(run_dir: Path, run_id: str, results: list[RunResult]) -> Path:
    """Write manifest.json, junit.xml, and report.html under run_dir; return the manifest path."""
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_dict(run_id, results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "junit.xml").write_text(junit_xml(results), encoding="utf-8")
    (run_dir / "report.html").write_text(html_report(run_id, results, run_dir), encoding="utf-8")
    return manifest_path

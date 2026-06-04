"""Reporting — manifest.json (the single source of truth) and JUnit XML.

A run executes one or more scenarios (list[RunResult]). The manifest records the
step/expect outcomes per scenario; JUnit feeds CI. Evidence artifacts are added
later (the evidence subsystem); the manifest already has a place for them.
"""

from __future__ import annotations

import html as _html
import json
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from bajutsu.orchestrator import RunResult


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


def _badge(ok: bool) -> str:
    return '<span class="pass">PASS</span>' if ok else '<span class="fail">FAIL</span>'


def _evidence(r: RunResult) -> str:
    """Embed the scenario's screen recording and link its other log artifacts
    (paths are relative to the run dir, where the report is written)."""
    parts: list[str] = []
    for a in r.artifacts:
        if a.kind == "video":
            parts.append(f'<video controls preload="metadata" src="{_html.escape(a.name, quote=True)}"></video>')
    links = [a for a in r.artifacts if a.kind != "video"]
    if links:
        items = "".join(
            f'<li><a href="{_html.escape(a.name, quote=True)}">{_html.escape(a.name)}</a>'
            f' <span class="kind">{_html.escape(a.kind)}</span></li>'
            for a in links
        )
        parts.append(f"<ul class='evidence'>{items}</ul>")
    return "".join(parts)


def _row(cells: list[str], ok: bool) -> str:
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return f"<tr class='{'ok' if ok else 'ng'}'>{tds}</tr>"


def html_report(run_id: str, results: list[RunResult]) -> str:
    """A self-contained HTML report (inline CSS, no external assets)."""
    e = _html.escape
    blocks: list[str] = []
    for r in results:
        rows = [
            _row([str(s.index), e(s.action), "ok" if s.ok else "FAIL",
                  f"{s.duration_s:.3f}s", e(s.reason)], s.ok)
            for s in r.steps
        ]
        rows += [
            _row(["expect", e(a.kind), "ok" if a.ok else "FAIL", "", e(a.reason)], a.ok)
            for a in r.expect_results
        ]
        blocks.append(
            f"<section><h2>{e(r.scenario)} {_badge(r.ok)}</h2>"
            f"{_evidence(r)}"
            f"<table><thead><tr><th>#</th><th>action</th><th>result</th>"
            f"<th>time</th><th>reason</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )
    style = (
        "body{font-family:-apple-system,system-ui,sans-serif;margin:2rem;color:#222}"
        "table{border-collapse:collapse;width:100%;margin:.5rem 0}"
        "th,td{border:1px solid #ddd;padding:.3rem .5rem;text-align:left;font-size:.9rem}"
        "tr.ng{background:#fff0f0}.pass{color:#0a0;font-weight:700}.fail{color:#c00;font-weight:700}"
        "video{max-width:320px;display:block;margin:.5rem 0;border:1px solid #ddd;border-radius:6px}"
        "ul.evidence{margin:.25rem 0;padding-left:1.2rem;font-size:.85rem}"
        ".kind{color:#888;font-size:.8rem}"
    )
    overall = all(r.ok for r in results)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Bajutsu run {e(run_id)}</title><style>{style}</style></head>"
        f"<body><h1>Bajutsu run {e(run_id)} {_badge(overall)}</h1>"
        f"{''.join(blocks)}</body></html>"
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
    (run_dir / "report.html").write_text(html_report(run_id, results), encoding="utf-8")
    return manifest_path

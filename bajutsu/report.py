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


def _run_backend(results: list[RunResult]) -> str:
    """The actuator that drove the run. One actuator is fixed per run, so this is
    normally a single name; if scenarios somehow differ, they are joined."""
    names = dict.fromkeys(r.backend for r in results if r.backend)  # ordered-unique
    return ", ".join(names)


def manifest_dict(run_id: str, results: list[RunResult]) -> dict[str, object]:
    """Build the manifest. RunResult and its parts are dataclasses, so asdict()
    captures step/expect outcomes verbatim. `backend` is the actuator that drove
    the run (each scenario also carries its own `backend`)."""
    return {
        "runId": run_id,
        "ok": all(r.ok for r in results),
        "backend": _run_backend(results),
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


def _step_evidence(s: Any, e: Any) -> str:
    """The per-step result artifacts: a screenshot thumbnail (opens a lightbox) and
    a link to the element tree captured after the step."""
    parts: list[str] = []
    shot = next((a for a in s.artifacts if a.kind == "screenshot"), None)
    if shot is not None:
        parts.append(
            f'<img class="shot" loading="lazy" src="{e(shot.name)}" alt="step {s.index} result">'
        )
    tree = next((a for a in s.artifacts if a.kind == "elements"), None)
    if tree is not None:
        parts.append(f'<a class="elnk" href="{e(tree.name)}" target="_blank" rel="noopener">tree</a>')
    return "".join(parts) or "—"


def _expects_table(label: str, rows: list[str]) -> str:
    return (
        f'<div class="expects"><span class="deflbl">{label}</span>'
        "<table class='extbl'><thead><tr><th>result</th><th>kind</th>"
        "<th>target</th><th>comparison</th><th>reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _expects_row(
    status_cls: str, status: str, kind: str, target: str, comp: str, reason: str, e: Any
) -> str:
    """One expectations table row. `target` / `comp` are pre-built HTML (tokenized);
    `reason` is plain text (escaped here). PASS/FAIL and the checked target/comparison
    are each in their own column."""
    rcls = status_cls or "skip"
    rsn = f'<span class="exreason">{e(reason)}</span>' if reason else ""
    return (
        f"<tr class='{rcls}'>"
        f'<td><span class="exst {status_cls}">{status}</span></td>'
        f'<td><span class="act act-assert">{e(kind)}</span></td>'
        f'<td class="adesc">{target}</td><td class="adesc">{comp}</td><td>{rsn}</td></tr>'
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


# Max response/request body chars embedded inline (the full body is in network.json).
_BODY_MAX = 4000


def _status_class(status: Any) -> str:
    if isinstance(status, int) and not isinstance(status, bool):
        if 200 <= status < 400:
            return "ok"
        if status >= 400:
            return "ng"
    return ""


def _kv_block(label: str, headers: Any, e: Any) -> str:
    """A request/response header block (skipped when empty)."""
    if not isinstance(headers, dict) or not headers:
        return ""
    rows = "".join(
        f'<div class="nxh"><span class="nxhk">{e(str(k))}</span>'
        f'<span class="nxhv">{e(str(v))}</span></div>'
        for k, v in headers.items()
    )
    return f'<div class="nxsec"><span class="nxlbl">{e(label)}</span>{rows}</div>'


def _body_block(label: str, body: Any, e: Any) -> str:
    """A request/response body block, truncated inline (skipped when empty)."""
    if not isinstance(body, str) or not body:
        return ""
    text = body if len(body) <= _BODY_MAX else body[:_BODY_MAX] + "\n… (truncated)"
    return f'<div class="nxsec"><span class="nxlbl">{e(label)}</span><pre class="nxpre">{e(text)}</pre></div>'


def _network_item(d: dict[str, Any], e: Any) -> str:
    """One captured exchange: a summary line (method / path / status / duration) that
    expands to the full url, headers, and bodies."""
    method = str(d.get("method") or "")
    status = d.get("status")
    target = str(d.get("path") or d.get("url") or "")
    dur = d.get("durationMs")
    dur_s = f"{float(dur):.0f} ms" if isinstance(dur, (int, float)) and not isinstance(dur, bool) else ""
    head = (
        f'<span class="nxm">{e(method)}</span>'
        f'<span class="nxp">{e(target)}</span>'
        f'<span class="nxs {_status_class(status)}">{e(str(status) if status is not None else "—")}</span>'
        f'<span class="nxd muted">{e(dur_s)}</span>'
    )
    sections: list[str] = []
    url = str(d.get("url") or "")
    if url and url != target:
        sections.append(f'<div class="nxsec"><span class="nxlbl">url</span><div class="nxh">{e(url)}</div></div>')
    sections.append(_kv_block("request headers", d.get("requestHeaders"), e))
    sections.append(_body_block("request body", d.get("requestBody"), e))
    sections.append(_kv_block("response headers", d.get("responseHeaders"), e))
    sections.append(_body_block("response body", d.get("responseBody"), e))
    err = d.get("error")
    if err:
        sections.append(f'<div class="nxsec"><span class="nxlbl">error</span><div class="nxh err">{e(str(err))}</div></div>')
    body = "".join(s for s in sections if s) or '<div class="muted">(no headers or body)</div>'
    return f'<details class="nx"><summary>{head}</summary><div class="nxbody">{body}</div></details>'


def _network_panel(run_dir: Path | None, art: Artifact, e: Any) -> str:
    """The exchanges BajutsuKit captured and POSTed to the collector (network.json)."""
    link = f'<a href="{e(art.name)}">{e(art.name)}</a>'
    data = _read_json(run_dir, art.name) if run_dir else None
    if not isinstance(data, list) or not data:
        return f'<div class="muted">network: {link} (no exchanges)</div>'
    items = "".join(_network_item(d, e) for d in data if isinstance(d, dict))
    n = sum(1 for d in data if isinstance(d, dict))
    plural = "exchange" if n == 1 else "exchanges"
    return (
        f'<div class="muted">{n} {plural} captured by BajutsuKit · {link}</div>'
        f'<div class="nxlist">{items}</div>'
    )


# --- scenario definition (rich view) ---

# action key (alias-cased, as dumped) -> (display label, color class)
_ACTION_META = {
    "tap": ("tap", "act-tap"),
    "doubleTap": ("double-tap", "act-tap"),
    "longPress": ("long-press", "act-tap"),
    "type": ("type", "act-type"),
    "swipe": ("swipe", "act-move"),
    "pinch": ("pinch", "act-move"),
    "rotate": ("rotate", "act-move"),
    "wait": ("wait", "act-wait"),
    "assert": ("assert", "act-assert"),
    "relaunch": ("relaunch", "act-wait"),
}


def _gnum(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _tok(cls: str, text: str, e: Any) -> str:
    """An inline value token (identifier / string / number) — styled distinctly from
    the solid action/assert badges so variables and constants read at a glance."""
    return f'<span class="tk {cls}">{e(text)}</span>'


def _pt(p: Any, e: Any) -> str:
    if isinstance(p, (list, tuple)) and len(p) == 2:
        return f"({_tok('num', _gnum(p[0]), e)}, {_tok('num', _gnum(p[1]), e)})"
    return "?"


def _sel_text(sel: dict[str, Any], e: Any) -> str:
    """A compact selector description as HTML; identifiers/constants are tokenized."""
    parts: list[str] = []
    if sel.get("id") is not None:
        parts.append(_tok("id", "#" + str(sel["id"]), e))
    if sel.get("idMatches") is not None:
        parts.append(_tok("id", "id~" + str(sel["idMatches"]), e))
    if sel.get("label") is not None:
        parts.append(_tok("str", f"“{sel['label']}”", e))
    if sel.get("labelMatches") is not None:
        parts.append(_tok("re", f"label~/{sel['labelMatches']}/", e))
    if sel.get("traits"):
        parts.append(_tok("kw", "[" + ", ".join(sel["traits"]) + "]", e))
    if sel.get("value") is not None:
        parts.append("value=" + _tok("str", f"“{sel['value']}”", e))
    if sel.get("index") is not None:
        parts.append(_tok("num", f"n={sel['index']}", e))
    if sel.get("within"):
        parts.append("within(" + _sel_text(sel["within"], e) + ")")
    return " ".join(parts) or "?"


def _textmatch(m: dict[str, Any], e: Any) -> str:
    for op, sign in (("equals", "=="), ("contains", "contains"), ("matches", "matches")):
        if m.get(op) is not None:
            return f"{sign} " + _tok("str", f"“{m[op]}”", e)
    return "?"


def _countmatch(m: dict[str, Any], e: Any) -> str:
    for op, sign in (("equals", "=="), ("atLeast", "≥"), ("atMost", "≤")):
        if m.get(op) is not None:
            return f"{sign} " + _tok("num", str(m[op]), e)
    return "?"


def _request_text(m: dict[str, Any], e: Any) -> tuple[str, str]:
    """(target, comparison) for a request matcher: the target is the matched method /
    endpoint (url or path), the comparison is the expected status / count. Shared by the
    `request` assertion and the `until: { request }` wait."""
    target: list[str] = []
    if m.get("method") is not None:
        target.append(_tok("kw", str(m["method"]).upper(), e))
    if m.get("url") is not None:
        target.append(_tok("str", str(m["url"]), e))
    if m.get("urlMatches") is not None:
        target.append(_tok("re", f"url~/{m['urlMatches']}/", e))
    if m.get("path") is not None:
        target.append(_tok("str", str(m["path"]), e))
    if m.get("pathMatches") is not None:
        target.append(_tok("re", f"path~/{m['pathMatches']}/", e))
    comp: list[str] = []
    if m.get("status") is not None:
        comp.append("status == " + _tok("num", str(m["status"]), e))
    if m.get("count") is not None:
        comp.append("count == " + _tok("num", str(m["count"]), e))
    return " ".join(target) or "?", " ".join(comp)


def _assert_parts(a: dict[str, Any], e: Any) -> tuple[str, str, str]:
    """Decompose one assertion into (kind, target, comparison) so each lands in its
    own table cell: e.g. `value` / `#ctrl.button.value` / `== “0”`. The comparison is
    empty for existence/state checks."""
    if "exists" in a:
        ex = a["exists"]
        sel = ex.get("sel", ex)
        return ("not exists" if ex.get("negate") else "exists"), _sel_text(sel, e), ""
    for kind in ("value", "label"):
        if kind in a:
            m = a[kind]
            return kind, _sel_text(m["sel"], e), _textmatch(m, e)
    if "count" in a:
        m = a["count"]
        return "count", _sel_text(m["sel"], e), _countmatch(m, e)
    for kind in ("enabled", "disabled", "selected"):
        if kind in a:
            return kind, _sel_text(a[kind], e), ""
    if "request" in a:
        target, comp = _request_text(a["request"], e)
        return "request", target, comp
    return "?", "", ""


def _assert_rows(payload: list[dict[str, Any]], e: Any) -> str:
    """A compact nested table for an `assert` step's assertions — one row each,
    split into kind / target / comparison cells (instead of a joined `a; b; c`)."""
    rows = "".join(
        f'<tr><td><span class="act act-assert">{e(k)}</span></td>'
        f'<td class="adesc">{t}</td><td class="adesc">{c}</td></tr>'
        for k, t, c in (_assert_parts(a, e) for a in payload)
    )
    return f'<table class="atbl"><tbody>{rows}</tbody></table>'


def _step_desc(action: str, payload: Any, e: Any) -> str:
    if action in ("tap", "doubleTap"):
        return _sel_text(payload, e)
    if action == "longPress":
        return f"{_sel_text(payload['sel'], e)} · " + _tok("num", f"{_gnum(payload['duration'])}s", e)
    if action == "type":
        s = _tok("str", f"“{payload.get('text', '')}”", e)
        if payload.get("into"):
            s += " into " + _sel_text(payload["into"], e)
        if payload.get("submit"):
            s += " + submit"
        return s
    if action == "swipe":
        if payload.get("on"):
            return f"{e(payload.get('direction', ''))} on {_sel_text(payload['on'], e)}"
        return f"{_pt(payload.get('from'), e)} → {_pt(payload.get('to'), e)}"
    if action == "pinch":
        return f"{_sel_text(payload['sel'], e)} · ×" + _tok("num", _gnum(payload["scale"]), e)
    if action == "rotate":
        return f"{_sel_text(payload['sel'], e)} · " + _tok("num", f"{_gnum(payload['radians'])} rad", e)
    if action == "wait":
        if payload.get("for"):
            cond = "for " + _sel_text(payload["for"], e)
        else:
            until = payload.get("until")
            if isinstance(until, dict) and "gone" in until:
                cond = "until gone " + _sel_text(until["gone"], e)
            elif isinstance(until, dict) and "request" in until:
                target, comp = _request_text(until["request"], e)
                cond = "until request " + " · ".join(p for p in (target, comp) if p and p != "?")
            else:
                cond = f"until {e(str(until))}"
        return f"{cond} (≤" + _tok("num", f"{_gnum(payload.get('timeout'))}s", e) + ")"
    if action == "assert":
        return _assert_rows(payload, e)
    return "relaunch" if action == "relaunch" else ""


def _preconditions_block(definition: dict[str, Any] | None, e: Any) -> str:
    """Preconditions as a collapsible key/value table."""
    pre = (definition or {}).get("preconditions") or {}
    rows: list[tuple[str, str]] = []
    if "erase" in pre:
        rows.append(("erase", "true" if pre["erase"] else "false"))
    if pre.get("deeplink"):
        rows.append(("deeplink", str(pre["deeplink"])))
    if pre.get("locale"):
        rows.append(("locale", str(pre["locale"])))
    if pre.get("setup"):
        rows.append(("setup", str(pre["setup"])))
    rows += [(str(k), str(v)) for k, v in (pre.get("launchEnv") or {}).items()]
    if pre.get("launchArgs"):
        rows.append(("launchArgs", " ".join(pre["launchArgs"])))
    if not rows:
        return ""
    body = "".join(f'<tr><td class="pk">{e(k)}</td><td>{e(v)}</td></tr>' for k, v in rows)
    return (
        f'<details class="pre" open><summary>preconditions ({len(rows)})</summary>'
        f'<table class="pretbl"><tbody>{body}</tbody></table></details>'
    )


def _step_action_cell(step_def: dict[str, Any] | None, out_action: str | None, e: Any) -> str:
    """The 'action' column: the action as a colored badge (from the definition, else
    the bare outcome action kind when no definition is available)."""
    if step_def is not None:
        action = next((k for k in _ACTION_META if k in step_def), None)
        if action is not None:
            label, cls = _ACTION_META[action]
            return f'<span class="act {cls}">{e(label)}</span>'
    if out_action:
        return f'<span class="act">{e(out_action)}</span>'
    return ""


def _step_detail_cell(step_def: dict[str, Any] | None, e: Any) -> str:
    """The 'detail' column: the tokenized target description (+ optional step name
    and capture tags). Empty without a definition."""
    if step_def is None:
        return ""
    action = next((k for k in _ACTION_META if k in step_def), None)
    if action is None:
        return ""
    desc = _step_desc(action, step_def[action], e)
    extra = f' <span class="stepname">{e(step_def["name"])}</span>' if step_def.get("name") else ""
    extra += "".join(f' <span class="cap">{e(c)}</span>' for c in (step_def.get("capture") or []))
    return desc + extra


def _merged_table(r: RunResult, plan: list[dict[str, Any]], e: Any) -> str:
    """One row per step (a table parallel to the expectations table): result / action
    / detail / time / view / reason.

    Driven by the plan so steps that never ran (execution stops at the first
    failure) still appear, marked as not run. Rows that did run stay clickable
    `srow`s tagged with their video offset (`data-t`)."""
    by_index = {s.index: s for s in r.steps}
    total = max(len(plan), len(r.steps))
    rows: list[str] = []
    for i in range(total):
        step_def = plan[i] if i < len(plan) else None
        out = by_index.get(i)
        action = _step_action_cell(step_def, out.action if out else None, e)
        detail = _step_detail_cell(step_def, e)
        if out is not None:
            tag = (
                f"<tr class='srow {'ok' if out.ok else 'ng'}' data-t='{out.started_at:.3f}'"
                f" title='jump to {out.started_at:.1f}s in the recording'>"
            )
            st = "ok" if out.ok else "ng"
            result = f'<span class="exst {st}">{"PASS" if out.ok else "FAIL"}</span>'
            at, view = f"{out.started_at:.1f}s", _step_evidence(out, e)
            reason = f'<span class="exreason">{e(out.reason)}</span>' if not out.ok and out.reason else ""
        else:
            tag = "<tr class='skip'>"
            result, at, view, reason = '<span class="exst">—</span>', "", "—", ""
        rows.append(
            f"{tag}<td>{i}</td><td>{result}</td><td>{action}</td>"
            f"<td class='adesc'>{detail}</td><td>{at}</td>"
            f"<td class='ev'>{view}</td><td>{reason}</td></tr>"
        )
    return (
        '<div class="steps-sec"><span class="deflbl">steps</span>'
        "<table class='sttbl'><thead><tr><th>#</th><th>result</th><th>action</th>"
        "<th>detail</th><th>at</th><th>view</th><th>reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _expects_view(r: RunResult, definition: dict[str, Any] | None, e: Any) -> str:
    """Expectations as a table with PASS/FAIL in its own column. Evaluated results
    win; if a step failed before expects ran, the planned expectations are shown as
    not-evaluated so they aren't lost. The condition reuses the tokenized assertion
    from the definition (ids/constants distinct from the kind badge)."""
    planned = (definition or {}).get("expect") or []
    if r.expect_results:
        rows = []
        for i, a in enumerate(r.expect_results):
            if i < len(planned):
                kind, target, comp = _assert_parts(planned[i], e)
            else:
                kind, target, comp = a.kind, str(e(a.detail)), ""
            rows.append(
                _expects_row(
                    "ok" if a.ok else "ng", "PASS" if a.ok else "FAIL",
                    kind, target, comp, a.reason if not a.ok else "", e,
                )
            )
        return _expects_table("expectations", rows)
    if not planned:
        return ""
    rows = [_expects_row("", "—", *_assert_parts(a, e), "", e) for a in planned]
    return _expects_table("expectations (not evaluated)", rows)


def _rich_view(r: RunResult, definition: dict[str, Any] | None, e: Any) -> str:
    plan = (definition or {}).get("steps") or []
    return _preconditions_block(definition, e) + _merged_table(r, plan, e) + _expects_view(r, definition, e)


def _merged_panel(
    r: RunResult, definition: dict[str, Any] | None, source: str | None, e: Any
) -> str:
    """The combined Steps+Scenario view, with a Rich/YAML toggle when YAML is given."""
    rich = _rich_view(r, definition, e)
    if not source:
        return rich
    return (
        '<div class="vtoggle"><button class="vt active" data-view="rich">Rich</button>'
        '<button class="vt" data-view="yaml">YAML</button></div>'
        f'<div class="view view-rich active">{rich}</div>'
        f'<div class="view view-yaml"><pre class="src">{e(source)}</pre></div>'
    )


def _scenario_section(
    i: int,
    r: RunResult,
    run_dir: Path | None,
    e: Any,
    definition: dict[str, Any] | None = None,
    source: str | None = None,
) -> str:
    video = _artifact(r, "video")
    media = (
        f'<video controls preload="metadata" src="{e(video.name)}"></video>'
        if video else '<div class="muted">no recording</div>'
    )
    # Steps and Scenario are merged into one tab (per-step plan + outcome).
    panels: list[tuple[str, str, str]] = [("steps", "Steps", _merged_panel(r, definition, source, e))]
    net = _artifact(r, "network")
    if net is not None:
        panels.append(("net", "Network", _network_panel(run_dir, net, e)))
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
    drv = f'<span class="drv" title="actuator backend">{e(r.backend)}</span>' if r.backend else ""
    return (
        f'<details class="scn" data-ok="{str(r.ok).lower()}"{open_attr}>'
        f'<summary><span class="dot{"" if r.ok else " ng"}"></span>'
        f'<span class="sname">{e(r.scenario)}</span> {drv}{_badge(r.ok)}</summary>'
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
.stats{display:flex;gap:.4rem;font-size:.82rem;align-items:center}
.chip{display:inline-flex;align-items:center;padding:.12rem .55rem;border-radius:999px;background:#3a3a3c}
.chip.ok{background:var(--ok)} .chip.ng{background:var(--ng)}
.chip.dchip{background:#2c5fb3;color:#fff;font-variant:small-caps;letter-spacing:.02em}
.drv{font:600 .68rem/1 ui-monospace,Menlo,Consolas,monospace;text-transform:uppercase;
 letter-spacing:.04em;color:#2c5fb3;background:#eaf0fb;border:1px solid #cfe0f8;
 border-radius:5px;padding:.1rem .35rem}
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
tr.srow{cursor:pointer}
tr.srow:hover td{background:#eef4ff}
tr.srow.playing td{background:#fff3c4;box-shadow:inset 3px 0 0 #f0a500}
td.ev{white-space:nowrap}
img.shot{height:52px;border:1px solid var(--line);border-radius:4px;vertical-align:middle;
 cursor:zoom-in;background:#fafafa}
.elnk{font-size:.78rem;margin-left:.4rem}
.lb{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;
 justify-content:center;z-index:50;cursor:zoom-out}
.lb.open{display:flex}
.lb-fig{margin:0;display:flex;flex-direction:column;align-items:center;gap:.6rem;cursor:default}
.lb img{max-width:84vw;max-height:84vh;border-radius:8px;box-shadow:0 10px 50px rgba(0,0,0,.6)}
.lb-cap{color:#e8e8e8;font:.84rem -apple-system,system-ui,sans-serif;text-align:center}
.lb-nav{flex:none;margin:0 .8rem;width:2.6rem;height:2.6rem;border:0;border-radius:50%;cursor:pointer;
 background:rgba(255,255,255,.14);color:#fff;font-size:1.7rem;line-height:1;display:flex;
 align-items:center;justify-content:center}
.lb-nav:hover{background:rgba(255,255,255,.28)}
.media{position:sticky;top:3.4rem}
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
.deflbl{display:block;margin-bottom:.35rem;font-size:.72rem;font-weight:700;text-transform:uppercase;
 letter-spacing:.05em;color:var(--mut)}
details.pre{margin-bottom:.6rem;border:1px solid var(--line);border-radius:8px;overflow:hidden}
details.pre>summary{display:block;cursor:pointer;padding:.32rem .6rem;background:#fafafb;
 font:700 .72rem -apple-system,system-ui,sans-serif;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}
details.pre>summary::before{content:"▸";display:inline-block;margin-right:.4rem;color:var(--mut);transition:transform .15s}
details.pre[open]>summary::before{transform:rotate(90deg)}
.pretbl td{border-bottom:1px solid var(--line);padding:.26rem .55rem;font-size:.82rem;vertical-align:top}
.pretbl tr:last-child td{border-bottom:0}
.pretbl td.pk{color:var(--mut);font:600 .8rem ui-monospace,Menlo,Consolas,monospace;width:1%;white-space:nowrap}
.act{display:inline-block;font:600 .72rem/1.4 -apple-system,system-ui,sans-serif;text-transform:uppercase;
 letter-spacing:.03em;color:#fff;background:#6b7280;border-radius:5px;padding:.08rem .42rem;min-width:4.3rem;
 text-align:center}
.act-tap{background:#2c5fb3} .act-type{background:#7a3aa8} .act-move{background:#0a7d6b}
.act-wait{background:#8a6d1a} .act-assert{background:#0a7d33}
.adesc{font:.84rem/1.45 ui-monospace,Menlo,Consolas,monospace;color:var(--ink);word-break:break-word}
/* Inline value tokens — subtle, lowercase, NOT solid badges, so ids (variables)
   and string/number literals (constants) are identifiable at a glance. */
.tk{font-family:inherit}
.tk.id{color:#3a4ba0;background:#eef1fc;border-radius:3px;padding:0 .2rem}
.tk.str{color:#9a5b00}
.tk.num{color:#0a6b6b}
.tk.re{color:#7a3aa8;font-style:italic}
.tk.kw{color:var(--mut)}
.stepname{font-size:.74rem;color:var(--mut);font-style:italic}
.cap{font-size:.68rem;color:#2c5fb3;background:#eaf0fb;border:1px solid #cfe0f8;border-radius:4px;
 padding:.02rem .3rem}
.expects{margin-top:.8rem}
.extbl td{vertical-align:top}
.extbl td:nth-child(-n+2),.extbl th:nth-child(-n+2),.extbl td:nth-child(4),.extbl th:nth-child(4){
 width:1%;white-space:nowrap}
.exst{display:inline-block;font:700 .68rem/1.4 -apple-system,system-ui,sans-serif;letter-spacing:.04em;
 color:#fff;background:var(--mut);border-radius:5px;padding:.06rem .42rem;min-width:3.1rem;text-align:center}
.exst.ok{background:var(--ok)} .exst.ng{background:var(--ng)}
.exreason{color:var(--ng);font-size:.8rem;font-style:italic}
.sttbl td:nth-child(-n+3),.sttbl th:nth-child(-n+3),.sttbl td:nth-child(5),.sttbl th:nth-child(5){
 width:1%;white-space:nowrap}
.sttbl td:first-child{color:var(--mut)}
/* Nested assertion table inside an `assert` step's detail cell (one row per check). */
.atbl{border-collapse:collapse;width:auto}
.atbl td{border:0;padding:.04rem .55rem .04rem 0;vertical-align:top}
.atbl td:first-child{width:1%;white-space:nowrap}
tr.skip td{color:var(--mut);opacity:.65}
.vtoggle{display:inline-flex;border:1px solid var(--line);border-radius:7px;overflow:hidden;margin-bottom:.6rem}
.vt{background:var(--card);color:var(--mut);border:0;cursor:pointer;font:600 .78rem -apple-system,system-ui,sans-serif;
 padding:.25rem .7rem}
.vt+.vt{border-left:1px solid var(--line)}
.vt.active{background:var(--ink);color:#fff}
.view{display:none} .view.active{display:block}
.src{background:#1c1c1e;color:#e6e6e6;border-radius:8px;padding:.6rem .75rem;max-height:460px;
 overflow:auto;font:12px/1.55 ui-monospace,Menlo,Consolas,monospace;white-space:pre;margin:0}
/* Network tab — the exchanges BajutsuKit captured (network.json). */
.nxlist{display:flex;flex-direction:column;gap:.3rem;margin-top:.45rem}
details.nx{border:1px solid var(--line);border-radius:7px;overflow:hidden;background:var(--card)}
details.nx>summary{list-style:none;cursor:pointer;display:flex;gap:.55rem;align-items:center;padding:.36rem .6rem}
details.nx>summary::-webkit-details-marker{display:none}
.nxm{font:600 .7rem/1.4 ui-monospace,Menlo,Consolas,monospace;text-transform:uppercase;color:#fff;
 background:#2c5fb3;border-radius:4px;padding:.06rem .4rem;min-width:3.4rem;text-align:center;flex:none}
.nxp{flex:1;font:.82rem ui-monospace,Menlo,Consolas,monospace;word-break:break-all;color:var(--ink)}
.nxs{flex:none;font:700 .7rem/1.4 -apple-system,system-ui,sans-serif;border-radius:4px;padding:.05rem .4rem;
 background:var(--mut);color:#fff;min-width:2.4rem;text-align:center}
.nxs.ok{background:var(--ok)} .nxs.ng{background:var(--ng)}
.nxd{flex:none}
.nxbody{padding:.15rem .6rem .55rem;border-top:1px solid var(--line);background:#fafafb}
.nxsec{margin-top:.5rem}
.nxlbl{display:block;font:700 .66rem -apple-system,system-ui,sans-serif;text-transform:uppercase;
 letter-spacing:.04em;color:var(--mut);margin-bottom:.22rem}
.nxh{display:flex;gap:.5rem;font:12px/1.55 ui-monospace,Menlo,Consolas,monospace;word-break:break-all}
.nxh.err{color:var(--ng)}
.nxhk{color:#3a4ba0;min-width:9rem;flex:none} .nxhv{color:var(--ink);word-break:break-all}
.nxpre{background:#1c1c1e;color:#e6e6e6;border-radius:7px;padding:.45rem .6rem;max-height:240px;overflow:auto;
 font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word;margin:0}
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
  // Rich / YAML toggle within the merged Steps tab.
  document.addEventListener('click', function(e){
    var t = e.target.closest('.vt'); if(!t) return;
    var panel = t.closest('.panel'), view = t.getAttribute('data-view');
    panel.querySelectorAll('.vt').forEach(function(b){ b.classList.toggle('active', b===t); });
    panel.querySelectorAll('.view').forEach(function(v){
      v.classList.toggle('active', v.classList.contains('view-'+view));
    });
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
  // Lightbox: click a step thumbnail to view it full-size, then ← / → walk through
  // every screenshot in the run (across scenarios). Esc or a backdrop click closes.
  var lb = document.getElementById('lb');
  var lbImg = lb && lb.querySelector('img');
  var lbCap = lb && lb.querySelector('.lb-cap');
  var lbIndex = -1;
  function lbShots(){ return Array.prototype.slice.call(document.querySelectorAll('img.shot')); }
  function lbShow(i){
    var arr = lbShots(); if(!arr.length || !lbImg) return;
    lbIndex = (i % arr.length + arr.length) % arr.length;   // wrap around
    var s = arr[lbIndex];
    lbImg.src = s.getAttribute('src');
    if(lbCap){
      var scn = s.closest('.scn'), row = s.closest('tr');
      var name = scn && scn.querySelector('.sname') ? scn.querySelector('.sname').textContent : '';
      var step = row && row.querySelector('td') ? row.querySelector('td').textContent : '';
      lbCap.textContent = name + (step !== '' ? '  ·  step ' + step : '') + '   ' + (lbIndex+1) + ' / ' + arr.length;
    }
    lb.classList.add('open');
  }
  function lbClose(){ if(!lb) return; lb.classList.remove('open'); if(lbImg) lbImg.removeAttribute('src'); lbIndex = -1; }
  window.openLightbox = function(src){
    var arr = lbShots(), i = 0;
    for(var k=0;k<arr.length;k++){ if(arr[k].getAttribute('src') === src){ i = k; break; } }
    lbShow(i);
  };
  if(lb){
    lb.addEventListener('click', function(e){ if(e.target === lb) lbClose(); });  // backdrop only
    var prev = lb.querySelector('.lb-prev'), next = lb.querySelector('.lb-next');
    if(prev) prev.addEventListener('click', function(){ lbShow(lbIndex - 1); });
    if(next) next.addEventListener('click', function(){ lbShow(lbIndex + 1); });
  }
  document.addEventListener('keydown', function(e){
    if(!lb || !lb.classList.contains('open')) return;
    if(e.key === 'Escape') lbClose();
    else if(e.key === 'ArrowLeft') lbShow(lbIndex - 1);
    else if(e.key === 'ArrowRight') lbShow(lbIndex + 1);
  });
  // Sync each scenario's recording with its step rows: click a step to seek there,
  // and highlight the step whose time window the playhead is in.
  document.querySelectorAll('.scn').forEach(function(scn){
    var v = scn.querySelector('video'); if(!v) return;
    var rows = Array.prototype.slice.call(scn.querySelectorAll('tr.srow'));
    if(!rows.length) return;
    rows.forEach(function(r){
      r.addEventListener('click', function(e){
        if(e.target.closest('a')) return;                 // links (element tree) work normally
        var shot = e.target.closest('.shot');
        if(shot){ openLightbox(shot.getAttribute('src')); return; }
        var t = parseFloat(r.getAttribute('data-t'));
        // Seek only: keep playing if already playing, stay paused if paused.
        if(!isNaN(t)){ v.currentTime = t; }
      });
    });
    v.addEventListener('timeupdate', function(){
      var ct = v.currentTime + 0.001, cur = null;
      for(var i=0;i<rows.length;i++){
        var t = parseFloat(rows[i].getAttribute('data-t'));
        if(!isNaN(t) && t <= ct) cur = rows[i];
      }
      rows.forEach(function(r){ r.classList.toggle('playing', r===cur); });
    });
  });
})();
"""


def html_report(
    run_id: str,
    results: list[RunResult],
    run_dir: Path | None = None,
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
) -> str:
    """A self-contained interactive HTML report (inline CSS + JS, no external assets).

    When `run_dir` is given the captured logs/traces are embedded inline (so the
    report works opened directly from disk); otherwise only the structure renders.
    `definitions` (structured) and `sources` (raw YAML), both aligned with
    `results`, drive the merged Steps tab and its Rich/YAML toggle.
    """
    e = _html.escape
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    overall = failed == 0
    backend = _run_backend(results)
    sections = "".join(
        _scenario_section(
            i, r, run_dir, e,
            definitions[i] if definitions and i < len(definitions) else None,
            sources[i] if sources and i < len(sources) else None,
        )
        for i, r in enumerate(results)
    )
    drv_chip = f'<span class="chip dchip" title="actuator backend">driver: {e(backend)}</span>' if backend else ""
    header = (
        f"<header><h1>Bajutsu run {e(run_id)} {_badge(overall)}</h1>"
        f'<div class="stats"><span class="chip ok">{passed} passed</span>'
        f'<span class="chip ng">{failed} failed</span>{drv_chip}</div>'
        '<div class="ctl"><label><input type="checkbox" onchange="onlyFailures(this)"> only failures</label>'
        '<button class="hbtn" onclick="toggleAll(true)">expand all</button>'
        '<button class="hbtn" onclick="toggleAll(false)">collapse all</button></div></header>'
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Bajutsu run {e(run_id)}</title><style>{_STYLE}</style></head>"
        f"<body>{header}<main>{sections}</main>"
        '<div class="lb" id="lb">'
        '<button class="lb-nav lb-prev" type="button" aria-label="previous screenshot">‹</button>'
        '<figure class="lb-fig"><img alt="step screenshot"><figcaption class="lb-cap"></figcaption></figure>'
        '<button class="lb-nav lb-next" type="button" aria-label="next screenshot">›</button>'
        '</div>'
        f"<script>{_SCRIPT}</script></body></html>"
    )


def write_report(
    run_dir: Path,
    run_id: str,
    results: list[RunResult],
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
) -> Path:
    """Write manifest.json, junit.xml, and report.html under run_dir; return the manifest path.

    `definitions` (structured) and `sources` (raw YAML), aligned with `results`,
    feed the report's merged Steps tab and its Rich/YAML toggle.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_dict(run_id, results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "junit.xml").write_text(junit_xml(results), encoding="utf-8")
    (run_dir / "report.html").write_text(
        html_report(run_id, results, run_dir, definitions, sources), encoding="utf-8"
    )
    return manifest_path

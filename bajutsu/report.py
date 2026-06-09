"""Reporting — manifest.json (the single source of truth), JUnit XML, and a
self-contained interactive HTML report.

A run executes one or more scenarios (list[RunResult]). The manifest records the
step/expect outcomes per scenario; JUnit feeds CI. report.html embeds the screen
recording and the captured logs (device log / app trace) with no external assets.

The HTML/CSS/JS live in `bajutsu/templates/` (report.html.j2 / report.css /
report.js); this module only turns RunResults into a pure-data context and renders
the Jinja template. Escaping is the template's job (autoescape), so nothing here
builds markup — the helpers below decompose selectors / assertions / exchanges into
small data structures (tokens, rows, panels) that the template renders.
"""

from __future__ import annotations

import functools
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from jinja2 import Environment, FileSystemLoader

from bajutsu.evidence import Artifact
from bajutsu.orchestrator import RunResult

# How many trailing log lines / body chars to embed inline (the full file is linked).
_LOG_MAX_LINES = 2000
_BODY_MAX = 4000

# An inline rich-text fragment: (token-class, text). An empty class means plain text.
Part = tuple[str, str]

_TEMPLATE_DIR = Path(__file__).parent / "templates"


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
    suite = ET.Element("testsuite", name="bajutsu", tests=str(len(results)), failures=str(failures))
    for r in results:
        case = ET.SubElement(suite, "testcase", name=r.scenario, classname="bajutsu")
        if not r.ok:
            failure = ET.SubElement(case, "failure", message=r.failure or "failed")
            failure.text = _details(r)
    return ET.tostring(suite, encoding="unicode")


# --- shared helpers (artifacts, files, formatting) ---


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


def _gnum(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _as_float(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _truncate(body: str) -> str:
    return body if len(body) <= _BODY_MAX else body[:_BODY_MAX] + "\n… (truncated)"


def _status_class(status: Any) -> str:
    if isinstance(status, int) and not isinstance(status, bool):
        if 200 <= status < 400:
            return "ok"
        if status >= 400:
            return "ng"
    return ""


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


# --- rich-text decomposition (selectors / matchers -> token parts) ---


def _join(*groups: list[Part]) -> list[Part]:
    """Concatenate non-empty part-groups, separated by a single space."""
    out: list[Part] = []
    for g in groups:
        if not g:
            continue
        if out:
            out.append(("", " "))
        out.extend(g)
    return out


def _sel_parts(sel: dict[str, Any]) -> list[Part]:
    groups: list[list[Part]] = []
    if sel.get("id") is not None:
        groups.append([("id", "#" + str(sel["id"]))])
    if sel.get("idMatches") is not None:
        groups.append([("id", "id~" + str(sel["idMatches"]))])
    if sel.get("label") is not None:
        groups.append([("str", f"“{sel['label']}”")])
    if sel.get("labelMatches") is not None:
        groups.append([("re", f"label~/{sel['labelMatches']}/")])
    if sel.get("traits"):
        groups.append([("kw", "[" + ", ".join(sel["traits"]) + "]")])
    if sel.get("value") is not None:
        groups.append([("", "value="), ("str", f"“{sel['value']}”")])
    if sel.get("index") is not None:
        groups.append([("num", f"n={sel['index']}")])
    if sel.get("within"):
        groups.append([("", "within("), *_sel_parts(sel["within"]), ("", ")")])
    return _join(*groups) or [("", "?")]


def _pt_parts(p: Any) -> list[Part]:
    if isinstance(p, (list, tuple)) and len(p) == 2:
        return [("", "("), ("num", _gnum(p[0])), ("", ", "), ("num", _gnum(p[1])), ("", ")")]
    return [("", "?")]


def _textmatch_parts(m: dict[str, Any]) -> list[Part]:
    for op, sign in (("equals", "=="), ("contains", "contains"), ("matches", "matches")):
        if m.get(op) is not None:
            return [("", f"{sign} "), ("str", f"“{m[op]}”")]
    return [("", "?")]


def _countmatch_parts(m: dict[str, Any]) -> list[Part]:
    for op, sign in (("equals", "=="), ("atLeast", "≥"), ("atMost", "≤")):
        if m.get(op) is not None:
            return [("", f"{sign} "), ("num", str(m[op]))]
    return [("", "?")]


def _request_parts(m: dict[str, Any]) -> tuple[list[Part], list[Part]]:
    """(target, comparison) for a request matcher — target is the matched method /
    endpoint, comparison is the expected status / count. Shared by the `request`
    assertion and the `until: { request }` wait."""
    tg: list[list[Part]] = []
    if m.get("method") is not None:
        tg.append([("kw", str(m["method"]).upper())])
    if m.get("url") is not None:
        tg.append([("str", str(m["url"]))])
    if m.get("urlMatches") is not None:
        tg.append([("re", f"url~/{m['urlMatches']}/")])
    if m.get("path") is not None:
        tg.append([("str", str(m["path"]))])
    if m.get("pathMatches") is not None:
        tg.append([("re", f"path~/{m['pathMatches']}/")])
    cg: list[list[Part]] = []
    if m.get("status") is not None:
        cg.append([("", "status == "), ("num", str(m["status"]))])
    if m.get("bodyMatches") is not None:
        cg.append([("", "body~"), ("re", f"/{m['bodyMatches']}/")])
    if m.get("count") is not None:
        cg.append([("", "count == "), ("num", str(m["count"]))])
    return _join(*tg) or [("", "?")], _join(*cg)


def _assert_parts(a: dict[str, Any]) -> tuple[str, list[Part], list[Part]]:
    """Decompose one assertion into (kind, target-parts, comparison-parts)."""
    if "exists" in a:
        ex = a["exists"]
        sel = ex.get("sel", ex)
        return ("not exists" if ex.get("negate") else "exists"), _sel_parts(sel), []
    for kind in ("value", "label"):
        if kind in a:
            m = a[kind]
            return kind, _sel_parts(m["sel"]), _textmatch_parts(m)
    if "count" in a:
        m = a["count"]
        return "count", _sel_parts(m["sel"]), _countmatch_parts(m)
    for kind in ("enabled", "disabled", "selected"):
        if kind in a:
            return kind, _sel_parts(a[kind]), []
    if "request" in a:
        target, comp = _request_parts(a["request"])
        return "request", target, comp
    return "?", [], []


def _step_desc_parts(action: str, payload: Any) -> list[Part]:
    """The tokenized detail for a single (non-assert) step action."""
    if action in ("tap", "doubleTap"):
        return _sel_parts(payload)
    if action == "longPress":
        return [*_sel_parts(payload["sel"]), ("", " · "), ("num", f"{_gnum(payload['duration'])}s")]
    if action == "type":
        out: list[Part] = [("str", f"“{payload.get('text', '')}”")]
        if payload.get("into"):
            out += [("", " into "), *_sel_parts(payload["into"])]
        if payload.get("submit"):
            out += [("", " + submit")]
        return out
    if action == "swipe":
        if payload.get("on"):
            return [("", f"{payload.get('direction', '')} on "), *_sel_parts(payload["on"])]
        return [*_pt_parts(payload.get("from")), ("", " → "), *_pt_parts(payload.get("to"))]
    if action == "pinch":
        return [*_sel_parts(payload["sel"]), ("", " · ×"), ("num", _gnum(payload["scale"]))]
    if action == "rotate":
        return [*_sel_parts(payload["sel"]), ("", " · "), ("num", f"{_gnum(payload['radians'])} rad")]
    if action == "wait":
        return _wait_parts(payload)
    if action == "relaunch":
        return [("", "relaunch")]
    return []


def _wait_parts(payload: dict[str, Any]) -> list[Part]:
    if payload.get("for"):
        cond: list[Part] = [("", "for "), *_sel_parts(payload["for"])]
    else:
        until = payload.get("until")
        if isinstance(until, dict) and "gone" in until:
            cond = [("", "until gone "), *_sel_parts(until["gone"])]
        elif isinstance(until, dict) and "request" in until:
            target, comp = _request_parts(until["request"])
            segs = [s for s in (target, comp) if s and s != [("", "?")]]
            cond = [("", "until request ")]
            for j, seg in enumerate(segs):
                if j:
                    cond.append(("", " · "))
                cond.extend(seg)
        else:
            cond = [("", f"until {until}")]
    return [*cond, ("", " (≤"), ("num", f"{_gnum(payload.get('timeout'))}s"), ("", ")")]


# --- detail / row data (the merged Result table) ---


def _step_detail(step_def: dict[str, Any] | None) -> dict[str, Any]:
    """The 'detail' cell content for a planned step: tokenized parts (or a nested
    assert table), plus the optional step name and capture tags."""
    empty: dict[str, Any] = {"kind": "parts", "parts": [], "name": None, "caps": []}
    if step_def is None:
        return empty
    action = next((k for k in _ACTION_META if k in step_def), None)
    if action is None:
        return empty
    name = step_def.get("name")
    caps = step_def.get("capture") or []
    if action == "assert":
        return {"kind": "asserts", "rows": [_assert_parts(a) for a in step_def["assert"]],
                "name": name, "caps": caps}
    return {"kind": "parts", "parts": _step_desc_parts(action, step_def[action]),
            "name": name, "caps": caps}


def _action_data(step_def: dict[str, Any] | None, out_action: str | None) -> dict[str, str] | None:
    if step_def is not None:
        action = next((k for k in _ACTION_META if k in step_def), None)
        if action is not None:
            label, cls = _ACTION_META[action]
            return {"label": label, "cls": cls}
    if out_action:
        return {"label": out_action, "cls": ""}
    return None


def _view_data(out: Any) -> dict[str, Any]:
    shot = next((a for a in out.artifacts if a.kind == "screenshot"), None)
    tree = next((a for a in out.artifacts if a.kind == "elements"), None)
    return {
        "shot": shot.name if shot else None,
        "tree": tree.name if tree else None,
        "alt": f"step {out.index} result",
    }


def _step_run_row(i: int, step_def: dict[str, Any] | None, out: Any) -> dict[str, Any]:
    return {
        "rowcls": f"srow {'ok' if out.ok else 'ng'}",
        "data_t": f"{out.started_at:.3f}",
        "title": f"jump to {out.started_at:.1f}s in the recording",
        "num": str(i), "numcls": None,
        "result": {"cls": "ok" if out.ok else "ng", "text": "PASS" if out.ok else "FAIL"},
        "action": _action_data(step_def, out.action),
        "detail": _step_detail(step_def),
        "at": f"{out.started_at:.1f}s",
        "view": _view_data(out),
        "reason": out.reason if (not out.ok and out.reason) else None,
        "expand": None,
    }


def _step_skip_row(i: int, step_def: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "rowcls": "skip", "data_t": None, "title": None,
        "num": str(i), "numcls": None,
        "result": {"cls": "", "text": "—"},
        "action": _action_data(step_def, None),
        "detail": _step_detail(step_def),
        "at": "", "view": None, "reason": None, "expand": None,
    }


def _nx_pairs(d: dict[str, Any], fields: list[tuple[str, str]]) -> list[tuple[str, dict[str, Any]]]:
    """Build (label, value) pairs for an exchange's collapsible settings table from a
    list of (label, key) — tokens for scalars, header lists, and body blocks."""
    pairs: list[tuple[str, dict[str, Any]]] = []
    for label, key in fields:
        v = d.get(key)
        if key == "method":
            pairs.append((label, {"kind": "parts", "parts": [("kw", str(v or "req"))]}))
        elif key == "endpoint":
            ep = str(d.get("url") or d.get("path") or "")
            if ep:
                pairs.append((label, {"kind": "parts", "parts": [("str", ep)]}))
        elif key == "status" and v is not None:
            pairs.append((label, {"kind": "parts", "parts": [("num", str(v))]}))
        elif key == "durationMs" and isinstance(v, (int, float)) and not isinstance(v, bool):
            pairs.append((label, {"kind": "parts", "parts": [("num", f"{v:.0f} ms")]}))
        elif key in ("requestHeaders", "responseHeaders") and isinstance(v, dict) and v:
            pairs.append((label, {"kind": "headers", "pairs": list(v.items())}))
        elif key in ("requestBody", "responseBody") and isinstance(v, str) and v:
            pairs.append((label, {"kind": "body", "text": _truncate(v)}))
    return pairs


def _exchange_summary(d: dict[str, Any], fallback: str) -> list[Part]:
    endpoint = str(d.get("url") or d.get("path") or "")
    return [("str", endpoint)] if endpoint else [("kw", fallback)]


def _request_row(d: dict[str, Any], at: float) -> dict[str, Any]:
    """A request row. Its detail cell is just the endpoint (a click target); the full
    settings table renders in a separate full-width row below (so it gets the whole
    width instead of the cramped detail column)."""
    method = str(d.get("method") or "req")
    pairs = _nx_pairs(d, [("method", "method"), ("endpoint", "endpoint"),
                          ("headers", "requestHeaders"), ("body", "requestBody")])
    return {
        "rowcls": "nrow xrow", "data_t": None, "title": None,
        "num": "→", "numcls": "nix", "result": None,
        "action": {"label": method, "cls": "act-net"},
        "detail": {"kind": "nxsummary", "summary": _exchange_summary(d, method), "name": None, "caps": []},
        "expand": {"pairs": pairs},
        "at": f"{at:.1f}s", "view": None, "reason": None,
    }


def _response_row(d: dict[str, Any], at: float) -> dict[str, Any]:
    status = d.get("status")
    pairs = _nx_pairs(d, [("status", "status"), ("duration", "durationMs"),
                          ("headers", "responseHeaders"), ("body", "responseBody")])
    return {
        "rowcls": "nrow xrow", "data_t": None, "title": None,
        "num": "←", "numcls": "nix",
        "result": {"cls": _status_class(status), "text": str(status) if status is not None else "—"},
        "action": {"label": "response", "cls": "act-net"},
        "detail": {"kind": "nxsummary", "summary": _exchange_summary(d, "response"), "name": None, "caps": []},
        "expand": {"pairs": pairs},
        "at": f"{at:.1f}s", "view": None, "reason": None,
    }


def _merged_rows(
    r: RunResult, plan: list[dict[str, Any]], exchanges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Step rows plus the observed exchanges (split request/response) interleaved by
    time offset; not-run steps trail at the end in plan order."""
    by_index = {s.index: s for s in r.steps}
    total = max(len(plan), len(r.steps))
    timed: list[tuple[float, int, dict[str, Any]]] = []
    skipped: list[dict[str, Any]] = []
    for i in range(total):
        step_def = plan[i] if i < len(plan) else None
        out = by_index.get(i)
        if out is None:
            skipped.append(_step_skip_row(i, step_def))
        else:
            timed.append((out.started_at, 0, _step_run_row(i, step_def, out)))
    for d in exchanges:
        t0 = _as_float(d.get("startedAt"))
        dur_s = _as_float(d.get("durationMs")) / 1000.0
        timed.append((t0, 1, _request_row(d, t0)))
        timed.append((t0 + dur_s, 2, _response_row(d, t0 + dur_s)))
    timed.sort(key=lambda x: (x[0], x[1]))
    return [row for _, _, row in timed] + skipped


def _preconditions_rows(definition: dict[str, Any] | None) -> list[tuple[str, str]]:
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
    return rows


def _expects_data(r: RunResult, definition: dict[str, Any] | None) -> dict[str, Any] | None:
    planned = (definition or {}).get("expect") or []
    if r.expect_results:
        rows: list[dict[str, Any]] = []
        for i, a in enumerate(r.expect_results):
            if i < len(planned):
                kind, target, comp = _assert_parts(planned[i])
            else:
                kind, target, comp = a.kind, [("", a.detail)], []
            cls = "ok" if a.ok else "ng"
            rows.append({
                "rowcls": cls, "stcls": cls, "status": "PASS" if a.ok else "FAIL",
                "kind": kind, "target": target, "comp": comp,
                "reason": a.reason if not a.ok else None,
            })
        return {"label": "expectations", "rows": rows}
    if not planned:
        return None
    rows = []
    for a in planned:
        kind, target, comp = _assert_parts(a)
        rows.append({"rowcls": "skip", "stcls": "", "status": "—", "kind": kind,
                     "target": target, "comp": comp, "reason": None})
    return {"label": "expectations (not evaluated)", "rows": rows}


# --- panel data (Result / Network / Device Log / App Trace) ---


def _exchange_host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _domain_allowed(host: str, domains: list[str]) -> bool:
    """No filter -> every exchange; otherwise the host must equal a listed domain or be
    a subdomain of one (`api.example.com` is allowed by `example.com`)."""
    if not domains:
        return True
    host = host.lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def _result_panel(
    r: RunResult, definition: dict[str, Any] | None, source: str | None,
    exchanges: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = (definition or {}).get("steps") or []
    return {
        "kind": "result", "key": "steps", "label": "Result", "source": source,
        "preconditions": _preconditions_rows(definition),
        "steprows": _merged_rows(r, plan, exchanges),
        "expects": _expects_data(r, definition),
    }


def _network_item(d: dict[str, Any]) -> dict[str, Any]:
    method = str(d.get("method") or "")
    status = d.get("status")
    target = str(d.get("path") or d.get("url") or "")
    dur = d.get("durationMs")
    started = d.get("startedAt")
    sections: list[dict[str, Any]] = []
    url = str(d.get("url") or "")
    if url and url != target:
        sections.append({"kind": "line", "label": "url", "text": url, "cls": ""})
    rh = d.get("requestHeaders")
    if isinstance(rh, dict) and rh:
        sections.append({"kind": "kv", "label": "request headers", "pairs": list(rh.items())})
    rb = d.get("requestBody")
    if isinstance(rb, str) and rb:
        sections.append({"kind": "pre", "label": "request body", "text": _truncate(rb)})
    sh = d.get("responseHeaders")
    if isinstance(sh, dict) and sh:
        sections.append({"kind": "kv", "label": "response headers", "pairs": list(sh.items())})
    sb = d.get("responseBody")
    if isinstance(sb, str) and sb:
        sections.append({"kind": "pre", "label": "response body", "text": _truncate(sb)})
    err = d.get("error")
    if err:
        sections.append({"kind": "line", "label": "error", "text": str(err), "cls": "err"})
    return {
        "method": method, "target": target,
        "at": f"{float(started):.1f}s" if isinstance(started, (int, float)) and not isinstance(started, bool) else "",
        "status": str(status) if status is not None else "—",
        "status_cls": _status_class(status),
        "dur": f"{float(dur):.0f} ms" if isinstance(dur, (int, float)) and not isinstance(dur, bool) else "",
        "sections": sections,
    }


def _network_panel(run_dir: Path | None, art: Artifact) -> dict[str, Any]:
    data = _read_json(run_dir, art.name) if run_dir else None
    if not isinstance(data, list) or not data:
        return {"kind": "network", "key": "net", "label": "Network", "empty": True, "link": art.name}
    items = [_network_item(d) for d in data if isinstance(d, dict)]
    return {
        "kind": "network", "key": "net", "label": "Network", "empty": False, "link": art.name,
        "count": len(items), "plural": "exchange" if len(items) == 1 else "exchanges",
        "exchanges": items,
    }


def _log_panel(run_dir: Path | None, art: Artifact) -> dict[str, Any]:
    lines, total = _read_lines(run_dir, art.name, _LOG_MAX_LINES) if run_dir else (None, 0)
    if lines is None:
        return {"kind": "log", "key": "log", "label": "Device Log", "link": art.name, "lines": None}
    shown = len(lines)
    note = f"showing last {shown} of {total} lines · " if total > shown else ""
    return {"kind": "log", "key": "log", "label": "Device Log", "link": art.name,
            "lines": lines, "shown": shown, "note": note}


def _trace_panel(run_dir: Path | None, art: Artifact) -> dict[str, Any]:
    data = _read_json(run_dir, art.name) if run_dir else None
    if not isinstance(data, list) or not data:
        return {"kind": "trace", "key": "trace", "label": "App Trace", "link": art.name, "empty": True}
    rows = [
        (str(d.get("name", "")), str(d.get("durationMs", "")), str(d.get("begin", "")), str(d.get("end", "")))
        for d in data if isinstance(d, dict)
    ]
    return {"kind": "trace", "key": "trace", "label": "App Trace", "link": art.name,
            "empty": False, "rows": rows}


def _scenario_data(
    r: RunResult, run_dir: Path | None,
    definition: dict[str, Any] | None, source: str | None,
) -> dict[str, Any]:
    video = _artifact(r, "video")
    net = _artifact(r, "network")
    net_data = _read_json(run_dir, net.name) if (net is not None and run_dir is not None) else None
    all_exchanges = [d for d in net_data if isinstance(d, dict)] if isinstance(net_data, list) else []
    domains = ((definition or {}).get("networkSteps") or {}).get("domains") or []
    step_exchanges = [
        d for d in all_exchanges if _domain_allowed(_exchange_host(str(d.get("url") or "")), domains)
    ]
    panels: list[dict[str, Any]] = [_result_panel(r, definition, source, step_exchanges)]
    if net is not None:
        panels.append(_network_panel(run_dir, net))
    dev = _artifact(r, "deviceLog")
    if dev is not None:
        panels.append(_log_panel(run_dir, dev))
    trace = _artifact(r, "appTrace")
    if trace is not None:
        panels.append(_trace_panel(run_dir, trace))
    return {
        "name": r.scenario, "ok": r.ok, "backend": r.backend, "open": not r.ok,
        "video": video.name if video else None, "panels": panels,
    }


# --- Jinja rendering ---


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


@functools.lru_cache(maxsize=2)
def _asset(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def html_report(
    run_id: str,
    results: list[RunResult],
    run_dir: Path | None = None,
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
) -> str:
    """A self-contained interactive HTML report (inline CSS + JS, no external assets).

    When `run_dir` is given the captured logs/traces are embedded inline (so the report
    works opened directly from disk); otherwise only the structure renders.
    `definitions` (structured) and `sources` (raw YAML), both aligned with `results`,
    drive the merged Result tab and its Rich/YAML toggle.
    """
    passed = sum(1 for r in results if r.ok)
    scenarios = [
        _scenario_data(
            r, run_dir,
            definitions[i] if definitions and i < len(definitions) else None,
            sources[i] if sources and i < len(sources) else None,
        )
        for i, r in enumerate(results)
    ]
    return _env().get_template("report.html.j2").render(
        run_id=run_id, passed=passed, failed=len(results) - passed, overall=passed == len(results),
        backend=_run_backend(results), css=_asset("report.css"), js=_asset("report.js"),
        scenarios=scenarios,
    )


def write_report(
    run_dir: Path,
    run_id: str,
    results: list[RunResult],
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
) -> Path:
    """Write manifest.json, junit.xml, and report.html under run_dir; return the manifest path.

    `definitions` (structured) and `sources` (raw YAML), aligned with `results`, feed
    the report's merged Result tab and its Rich/YAML toggle.
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

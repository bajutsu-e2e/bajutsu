"""Rich-text decomposition.

Turn selectors / matchers / assertions into (class, text) token parts the template renders.
"""

from __future__ import annotations

from typing import Any

from bajutsu.report.format import Part, _gnum

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
    """(target, comparison) for a request matcher.

    Target is the matched method / endpoint, comparison is the expected status / count. Shared by
    the `request` assertion and the `until: { request }` wait.
    """
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
    if "visual" in a:
        m = a["visual"]
        vcomp: list[Part] = [("", "≤ "), ("num", f"{_gnum(m.get('threshold', 0))}%")]
        if m.get("exclude"):
            vcomp += [("", " · "), ("kw", f"{len(m['exclude'])} excluded")]
        return "visual", [("str", str(m.get("baseline", "?")))], vcomp
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
        return [
            *_sel_parts(payload["sel"]),
            ("", " · "),
            ("num", f"{_gnum(payload['radians'])} rad"),
        ]
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

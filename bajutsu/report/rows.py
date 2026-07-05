"""Row/detail data for the merged Result table (steps, network exchanges, expectations)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bajutsu.from_grouping import grouped_provenance
from bajutsu.orchestrator import RunResult
from bajutsu.report.format import (
    _ACTION_META,
    Part,
    _as_float,
    _gnum,
    _read_json,
    _status_class,
    _truncate,
)
from bajutsu.report.richtext import (
    _assert_parts,
    _step_desc_parts,
)

# --- detail / row data (the merged Result table) ---


def _step_detail(step_def: dict[str, Any] | None, from_: str | None = None) -> dict[str, Any]:
    """The 'detail' cell content for a planned step.

    Tokenized parts (or a nested assert table), plus the optional step name, capture tags, and
    `from:` provenance.

    `from_` is the already-grouped provenance to show (None when this step continues a run of the
    same phrase), not the step's raw `from:` — the caller dedupes consecutive equal values.
    """
    empty: dict[str, Any] = {"kind": "parts", "parts": [], "name": None, "caps": [], "from_": None}
    if step_def is None:
        return empty
    action = next((k for k in _ACTION_META if k in step_def), None)
    if action is None:
        return empty
    name = step_def.get("name")
    caps = step_def.get("capture") or []
    if action == "assert":
        return {
            "kind": "asserts",
            "rows": [_assert_parts(a) for a in step_def["assert"]],
            "name": name,
            "caps": caps,
            "from_": from_,
        }
    return {
        "kind": "parts",
        "parts": _step_desc_parts(action, step_def[action]),
        "name": name,
        "caps": caps,
        "from_": from_,
    }


def _action_data(step_def: dict[str, Any] | None, out_action: str | None) -> dict[str, str] | None:
    if step_def is not None:
        action = next((k for k in _ACTION_META if k in step_def), None)
        if action is not None:
            label, cls = _ACTION_META[action]
            return {"label": label, "cls": cls}
    if out_action:
        return {"label": out_action, "cls": ""}
    return None


def _tree_row(e: dict[str, Any]) -> dict[str, Any]:
    """One captured element rendered as a row for the in-report element viewer.

    `rect` carries the raw frame (points) so the viewer can highlight it on the screenshot.
    """
    frame = e.get("frame")
    fr = ""
    rect: dict[str, str] | None = None
    if isinstance(frame, (list, tuple)) and len(frame) == 4:
        x, y, w, h = frame
        fr = f"{_gnum(x)}, {_gnum(y)} · {_gnum(w)}×{_gnum(h)}"
        rect = {"x": _gnum(x), "y": _gnum(y), "w": _gnum(w), "h": _gnum(h)}
    val = e.get("value")
    return {
        "id": e.get("identifier") or "",
        "label": e.get("label") or "",
        "value": "" if val is None else str(val),
        "traits": " ".join(e.get("traits") or []),
        "frame": fr,
        "rect": rect,
    }


def _screen_rect(elements: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """The screen extent in points — the bounding box of every element frame.

    The element viewer maps a hovered frame onto the (full-screen) screenshot as a percentage of
    this, so it needs no device scale. The JS refines the height from the screenshot's true pixel
    size, so a long scrolling list does not distort the mapping.
    """
    w = h = 0.0
    for e in elements:
        fr = e.get("frame")
        if isinstance(fr, (list, tuple)) and len(fr) == 4:
            x, y, fw, fh = (_as_float(v) for v in fr)
            w, h = max(w, x + fw), max(h, y + fh)
    if w <= 0 or h <= 0:
        return None, None
    return _gnum(w), _gnum(h)


def _view_data(out: Any, run_dir: Path | None) -> dict[str, Any]:
    # Build a kind -> artifact index once so later lookups are O(1) instead of repeated
    # O(n) scans over the same list. Use setdefault so the first artifact of each kind
    # wins, matching the previous next(...) semantics (a kind can appear more than once
    # when e.g. screenshot.before and screenshot.after are both requested).
    by_kind: dict[str, Any] = {}
    for a in out.artifacts:
        by_kind.setdefault(a.kind, a)
    shot = by_kind.get("screenshot")
    tree = by_kind.get("elements")
    # Embed the captured elements inline so the report shows them in an overlay (no
    # new tab), matching how logs/network are embedded for offline (file://) viewing.
    tree_rows: list[dict[str, Any]] | None = None
    screen_w = screen_h = None
    if tree is not None and run_dir is not None:
        data = _read_json(run_dir, tree.name)
        if isinstance(data, list):
            els = [e for e in data if isinstance(e, dict)]
            tree_rows = [_tree_row(e) for e in els]
            screen_w, screen_h = _screen_rect(els)
    return {
        "shot": shot.name if shot else None,
        "tree": tree.name if tree else None,
        "tree_rows": tree_rows,
        "tree_count": len(tree_rows) if tree_rows is not None else 0,
        "screen_w": screen_w,
        "screen_h": screen_h,
        "alt": f"step {out.index} result",
    }


def _step_run_row(
    i: int,
    step_def: dict[str, Any] | None,
    out: Any,
    run_dir: Path | None,
    from_: str | None = None,
) -> dict[str, Any]:
    return {
        "rowcls": f"srow {'ok' if out.ok else 'ng'}",
        "data_t": f"{out.started_at:.3f}",
        "title": f"jump to {out.started_at:.1f}s in the recording",
        "num": str(i),
        "numcls": None,
        "result": {"cls": "ok" if out.ok else "ng", "text": "PASS" if out.ok else "FAIL"},
        "action": _action_data(step_def, out.action),
        "detail": _step_detail(step_def, from_),
        "at": f"{out.started_at:.1f}s",
        "view": _view_data(out, run_dir),
        "reason": out.reason if (not out.ok and out.reason) else None,
        "expand": None,
        "alerts": [{"label": a.label} for a in out.alerts],
    }


def _step_skip_row(
    i: int, step_def: dict[str, Any] | None, from_: str | None = None
) -> dict[str, Any]:
    return {
        "rowcls": "skip",
        "data_t": None,
        "title": None,
        "num": str(i),
        "numcls": None,
        "result": {"cls": "", "text": "—"},
        "action": _action_data(step_def, None),
        "detail": _step_detail(step_def, from_),
        "at": "",
        "view": None,
        "reason": None,
        "expand": None,
    }


def _nx_pairs(d: dict[str, Any], fields: list[tuple[str, str]]) -> list[tuple[str, dict[str, Any]]]:
    """Build (label, value) pairs for an exchange's collapsible settings table.

    Built from a list of (label, key) — tokens for scalars, header lists, and body blocks.
    """
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
    """A request row.

    Its detail cell is just the endpoint (a click target); the full settings table renders in a
    separate full-width row below (so it gets the whole width instead of the cramped detail column).
    """
    method = str(d.get("method") or "req")
    pairs = _nx_pairs(
        d,
        [
            ("method", "method"),
            ("endpoint", "endpoint"),
            ("headers", "requestHeaders"),
            ("body", "requestBody"),
        ],
    )
    return {
        "rowcls": "nrow xrow",
        "data_t": None,
        "title": None,
        "num": "→",
        "numcls": "nix",
        "result": None,
        "action": {"label": method, "cls": "act-net"},
        "detail": {
            "kind": "nxsummary",
            "summary": _exchange_summary(d, method),
            "name": None,
            "caps": [],
        },
        "expand": {"pairs": pairs},
        "at": f"{at:.1f}s",
        "view": None,
        "reason": None,
    }


def _response_row(d: dict[str, Any], at: float) -> dict[str, Any]:
    status = d.get("status")
    pairs = _nx_pairs(
        d,
        [
            ("status", "status"),
            ("duration", "durationMs"),
            ("headers", "responseHeaders"),
            ("body", "responseBody"),
        ],
    )
    return {
        "rowcls": "nrow xrow",
        "data_t": None,
        "title": None,
        "num": "←",
        "numcls": "nix",
        "result": {
            "cls": _status_class(status),
            "text": str(status) if status is not None else "—",
        },
        "action": {"label": "response", "cls": "act-net"},
        "detail": {
            "kind": "nxsummary",
            "summary": _exchange_summary(d, "response"),
            "name": None,
            "caps": [],
        },
        "expand": {"pairs": pairs},
        "at": f"{at:.1f}s",
        "view": None,
        "reason": None,
    }


def _merged_rows(
    r: RunResult,
    plan: list[dict[str, Any]],
    exchanges: list[dict[str, Any]],
    run_dir: Path | None,
) -> list[dict[str, Any]]:
    """Step rows plus the observed exchanges (split request/response) interleaved by time offset.

    Not-run steps trail at the end in plan order.
    """
    by_index = {s.index: s for s in r.steps}
    total = max(len(plan), len(r.steps))
    # Provenance to display per step, grouped in plan order so a run of identical consecutive
    # `from:` is labeled once (BE-0044); each step keeps its own value regardless of time sorting.
    shown_from = grouped_provenance(
        [(plan[i].get("from") if i < len(plan) else None) for i in range(total)]
    )
    timed: list[tuple[float, int, dict[str, Any]]] = []
    skipped: list[dict[str, Any]] = []
    for i in range(total):
        step_def = plan[i] if i < len(plan) else None
        out = by_index.get(i)
        if out is None:
            skipped.append(_step_skip_row(i, step_def, shown_from[i]))
        else:
            timed.append(
                (out.started_at, 0, _step_run_row(i, step_def, out, run_dir, shown_from[i]))
            )
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


def _visual_row(ev: Any, ok: bool) -> dict[str, Any] | None:
    """The baseline/actual/diff image strip for a `visual` expectation.

    `ev` is the AssertionResult.visual evidence (run-dir-relative image paths). The Approve button
    (functional only under `serve`) is offered whenever the comparison did not pass.
    """
    if ev is None:
        return None
    sid = ev.actual.rsplit("/", 1)[0] if "/" in ev.actual else ""
    return {
        "baseline": ev.baseline,
        "actual": ev.actual,
        "diff": ev.diff,
        "diff_pct": f"{ev.diff_pct:.2f}%" if ev.diff_pct is not None else None,
        "missing": ev.missing,
        "approvable": not ok,
        "baseline_name": ev.baseline_name,
        "sid": sid,
        "engine": ev.engine,
    }


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
            rows.append(
                {
                    "rowcls": cls,
                    "stcls": cls,
                    "status": "PASS" if a.ok else "FAIL",
                    "kind": kind,
                    "target": target,
                    "comp": comp,
                    "reason": a.reason if not a.ok else None,
                    "visual": _visual_row(a.visual, a.ok),
                }
            )
        return {
            "label": "expectations",
            "rows": rows,
            "alerts": [{"label": a.label} for a in r.expect_alerts],
        }
    if not planned:
        return None
    rows = []
    for a in planned:
        kind, target, comp = _assert_parts(a)
        rows.append(
            {
                "rowcls": "skip",
                "stcls": "",
                "status": "—",
                "kind": kind,
                "target": target,
                "comp": comp,
                "reason": None,
            }
        )
    return {"label": "expectations (not evaluated)", "rows": rows}

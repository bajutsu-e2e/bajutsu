"""`bajutsu trace` — inspect a finished run as a text timeline.

A read-only view over a run directory: per scenario, the steps and observed network
exchanges interleaved chronologically (by offset from the scenario's start), then the
expectations, app-trace intervals, and an evidence summary. Reads the persisted
manifest.json (+ network.json / appTrace.json), so it works on any saved run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def latest_run(runs_root: Path) -> Path | None:
    """The most recent run directory (timestamp-named) holding a manifest.json."""
    candidates = sorted(
        (p for p in runs_root.glob("*") if p.is_dir() and (p / "manifest.json").exists()),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _artifact(scenario: dict[str, Any], kind: str) -> str | None:
    for art in scenario.get("artifacts") or []:
        if art.get("kind") == kind:
            return str(art.get("name"))
    return None


def _at(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _step_event(step: dict[str, Any]) -> tuple[float, str]:
    mark = "✓" if step.get("ok") else "✗"
    desc = f"{mark} {step.get('action', '')!s:<9}"
    dur = step.get("duration_s")
    if isinstance(dur, (int, float)) and not isinstance(dur, bool):
        desc += f"  ({dur:.2f}s)"
    if not step.get("ok") and step.get("reason"):
        desc += f"   ✗ {step['reason']}"
    return _at(step.get("started_at")), desc


def _net_event(exchange: dict[str, Any]) -> tuple[float, str]:
    method = str(exchange.get("method") or "")
    target = str(exchange.get("path") or exchange.get("url") or "")
    status = exchange.get("status")
    desc = f"net  {method:<6} {target} → {status if status is not None else '—'}"
    dur = exchange.get("durationMs")
    if isinstance(dur, (int, float)) and not isinstance(dur, bool):
        desc += f"  {dur:.0f}ms"
    if exchange.get("mocked"):
        desc += "  [mock]"
    return _at(exchange.get("startedAt")), desc


def _scenario_lines(run_dir: Path, scenario: dict[str, Any]) -> list[str]:
    grade = "PASS" if scenario.get("ok") else "FAIL"
    lines = [f"▸ {scenario.get('scenario', '')}   {grade}   [{scenario.get('backend', '')}]"]

    events: list[tuple[float, str]] = [_step_event(s) for s in scenario.get("steps") or []]
    net_name = _artifact(scenario, "network")
    network = _read_json(run_dir / net_name) if net_name else None
    if isinstance(network, list):
        events += [_net_event(ex) for ex in network if isinstance(ex, dict)]
    if events:
        lines.append("  timeline:")
        for at, desc in sorted(events, key=lambda e: e[0]):
            lines.append(f"    {at:>5.1f}s  {desc}")

    expects = scenario.get("expect_results") or []
    if expects:
        lines.append("  expectations:")
        for e in expects:
            mark = "✓" if e.get("ok") else "✗"
            line = f"    {mark} {e.get('kind', '')!s:<8} {e.get('detail', '')}"
            if not e.get("ok") and e.get("reason"):
                line += f"   ✗ {e['reason']}"
            lines.append(line)

    trace_name = _artifact(scenario, "appTrace")
    intervals = _read_json(run_dir / trace_name) if trace_name else None
    if isinstance(intervals, list) and intervals:
        lines.append("  appTrace:")
        for it in intervals:
            if isinstance(it, dict):
                lines.append(f"    {it.get('name', '')}   {it.get('durationMs', '?')}ms")

    kinds = sorted({str(a.get("kind")) for a in scenario.get("artifacts") or []})
    if kinds:
        lines.append(f"  evidence: {' · '.join(kinds)}")
    if not scenario.get("ok") and scenario.get("failure"):
        lines.append(f"  failure: {scenario['failure']}")
    lines.append("")
    return lines


def trace_run(run_dir: Path, scenario_filter: str | None = None) -> str:
    """Render the run at `run_dir` as a text timeline. `scenario_filter` (substring,
    case-insensitive) limits which scenarios are shown."""
    manifest = _read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        return f"no readable manifest.json in {run_dir}"
    grade = "PASS" if manifest.get("ok") else "FAIL"
    out = [f"bajutsu trace · run {manifest.get('runId', '')} · {grade} · driver: {manifest.get('backend', '')}", ""]
    for scenario in manifest.get("scenarios") or []:
        name = str(scenario.get("scenario", ""))
        if scenario_filter and scenario_filter.lower() not in name.lower():
            continue
        out += _scenario_lines(run_dir, scenario)
    return "\n".join(out).rstrip() + "\n"

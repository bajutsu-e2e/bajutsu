"""`bajutsu trace` — inspect a finished run as a text timeline, or `--explain` a scenario.

A read-only view over a run directory: per scenario, the steps and observed network
exchanges interleaved chronologically (by offset from the scenario's start), then the
expectations, app-trace intervals, and an evidence summary. Reads the persisted
manifest.json (+ network.json / appTrace.json), so it works on any saved run.

The `--explain` dry run is the pre-run counterpart (BE-0028): given a scenario, it previews how
each `capturePolicy` rule would fire — counting action-triggered rules exactly (reusing the run
loop's own matcher) and flagging heavy captures on broadly-matching rules — so an author can
tighten a glob before a run quietly produces gigabytes. Advisory only; never touches pass/fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bajutsu.intervals import INTERVAL_KINDS
from bajutsu.provenance import grouped_provenance
from bajutsu.scenario import load_scenario_file

if TYPE_CHECKING:
    from bajutsu.scenario import CaptureRule, Scenario, Step

# Captures expensive enough to warn about on a broad rule: the scenario-wide interval
# recordings plus the network collector. (screenshot / elements / actionLog are cheap instants.)
# The run loop's matcher (`_rule_fires`, `_action_of`, …) is imported lazily inside the `--explain`
# helpers below, so plain `bajutsu trace <run-dir>` (the timeline) never pulls in the orchestrator.
_HEAVY_KINDS = INTERVAL_KINDS | {"network"}


@dataclass(frozen=True)
class RuleExplain:
    """How one capturePolicy rule would fire.

    `countable` action rules carry an exact `count` and the `steps` they match; `event`/`result`
    rules are runtime-dependent (reported, not counted). `warn` = a heavy capture on a
    broadly-matching rule.
    """

    trigger: str
    capture: list[str]
    countable: bool
    count: int = 0
    steps: list[str] = field(default_factory=list)
    heavy: list[str] = field(default_factory=list)
    broad: bool = False

    @property
    def warn(self) -> bool:
        return bool(self.heavy) and self.broad


def _step_label(step: Step, index: int) -> str:
    """A short, stable identifier for a step in the report (its name, or action + primary id)."""
    if step.name:
        return f"#{index} {step.name}"
    from bajutsu.orchestrator.actions._registry import _action_of
    from bajutsu.orchestrator.evidence_rules import _primary_selector

    primary = _primary_selector(step)
    target = primary.id if primary is not None and primary.id is not None else ""
    return f"#{index} {_action_of(step)}{f' {target}' if target else ''}"


def _trigger_desc(rule: CaptureRule) -> str:
    on = rule.on
    if on.action is not None:
        return f"action={on.action}" + (f" idMatches={on.id_matches}" if on.id_matches else "")
    if on.event is not None:
        return f"event={on.event}"
    return f"result={on.result}"


def _is_broad(rule: CaptureRule) -> bool:
    """Whether a rule matches broadly — not pinned to a specific element.

    An action rule with no `idMatches` (every step of that action) or a leading-`*` glob, or a
    `screenChanged` event. `result: error` is the safety net — rare by design, so not broad.
    """
    on = rule.on
    if on.action is not None:
        return on.id_matches is None or on.id_matches.startswith("*")
    return on.event == "screenChanged"


def explain_capture(scenario: Scenario) -> list[RuleExplain]:
    """Statically classify how each of a scenario's capturePolicy rules would fire."""
    out: list[RuleExplain] = []
    for rule in scenario.capture_policy:
        # a capture token is `<kind>[.<modifier>]`; the kind decides whether it is heavy
        heavy = [k for k in rule.capture if k.partition(".")[0] in _HEAVY_KINDS]
        broad = _is_broad(rule)
        if rule.on.action is None:
            # event / result: the run loop decides at runtime; we can't count statically.
            out.append(
                RuleExplain(
                    trigger=_trigger_desc(rule),
                    capture=list(rule.capture),
                    countable=False,
                    heavy=heavy,
                    broad=broad,
                )
            )
            continue
        steps = [
            _step_label(step, i) for i, step in enumerate(scenario.steps) if _step_fires(rule, step)
        ]
        out.append(
            RuleExplain(
                trigger=_trigger_desc(rule),
                capture=list(rule.capture),
                countable=True,
                count=len(steps),
                steps=steps,
                heavy=heavy,
                broad=broad,
            )
        )
    return out


def _step_fires(rule: CaptureRule, step: Step) -> bool:
    """Whether an action-triggered `rule` fires on `step`.

    Uses the run loop's own matcher with neutral runtime signals, so the count matches what a real
    run would record.
    """
    from bajutsu.orchestrator.actions._registry import _action_of
    from bajutsu.orchestrator.evidence_rules import _primary_selector, _rule_fires

    primary = _primary_selector(step)
    primary_id = primary.id if primary is not None else None
    return _rule_fires(rule, _action_of(step), primary_id, screen_changed=False, ok=True)


def render_explain(scenarios: list[Scenario]) -> str:
    """Render the dry-run report for one or more scenarios."""
    lines: list[str] = []
    for scenario in scenarios:
        lines.append(f"scenario: {scenario.name}")
        rules = explain_capture(scenario)
        if not rules:
            lines.append("  (no capturePolicy rules)")
            continue
        for rule in rules:
            heavy = f" [heavy: {', '.join(rule.heavy)}]" if rule.heavy else ""
            warn = " ⚠ heavy capture on a broad rule — tighten the match" if rule.warn else ""
            if rule.countable:
                lines.append(f"  {rule.trigger}: fires {rule.count}×{heavy}{warn}")
                lines.extend(f"      {s}" for s in rule.steps)
            else:
                lines.append(f"  {rule.trigger}: fires at runtime (conditional){heavy}{warn}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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


def _step_event(step: dict[str, Any], from_: str | None = None) -> tuple[float, str]:
    mark = "✓" if step.get("ok") else "✗"
    desc = f"{mark} {step.get('action', '')!s:<9}"
    dur = step.get("duration_s")
    if isinstance(dur, (int, float)) and not isinstance(dur, bool):
        desc += f"  ({dur:.2f}s)"
    if from_:  # the natural-language phrase this step was recorded from (BE-0044), if shown here
        desc += f'   ← "{from_}"'
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


def _provenance_by_scenario(run_dir: Path) -> dict[str, list[str | None]]:
    """Each scenario's `from:` phrase per top-level step, in order (None where absent), from `scenario.yaml`.

    The timeline keys provenance by each manifest step's `index`, which the run loop assigns over a
    flat counter that also counts steps nested in `if`/`forEach`; our list is top-level only, so the
    two index spaces diverge once a control-flow step runs. Rather than mislabel, a scenario that uses
    control flow is omitted (it simply shows no phrases). Provenance is metadata the timeline only
    displays; an older run with no `scenario.yaml` (or an unreadable one) yields none, so the timeline
    still renders (BE-0068's spirit).
    """
    try:
        text = (run_dir / "scenario.yaml").read_text(encoding="utf-8")
        scenarios = load_scenario_file(text).scenarios
    except (OSError, ValueError):
        return {}
    return {
        s.name: [st.from_ for st in s.steps]
        for s in scenarios
        if not any(st.if_ is not None or st.for_each is not None for st in s.steps)
    }


def _step_index(step: dict[str, Any]) -> int:
    # A missing/invalid index returns -1 (never a valid plan position), so the caller's bounds check
    # omits provenance rather than mislabeling the step with step 0's phrase.
    idx = step.get("index")
    return idx if isinstance(idx, int) and not isinstance(idx, bool) else -1


def _scenario_lines(
    run_dir: Path, scenario: dict[str, Any], plan_froms: list[str | None] | None = None
) -> list[str]:
    grade = "PASS" if scenario.get("ok") else "FAIL"
    lines = [f"▸ {scenario.get('scenario', '')}   {grade}   [{scenario.get('backend', '')}]"]

    steps = scenario.get("steps") or []
    # Group over the full plan so the label collapses the same way the report does, then show each
    # executed step the value at its plan index.
    shown = grouped_provenance(plan_froms or [])
    events: list[tuple[float, str]] = [
        _step_event(s, shown[i] if 0 <= (i := _step_index(s)) < len(shown) else None) for s in steps
    ]
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
        lines.extend(
            f"    {it.get('name', '')}   {it.get('durationMs', '?')}ms"
            for it in intervals
            if isinstance(it, dict)
        )

    kinds = sorted({str(a.get("kind")) for a in scenario.get("artifacts") or []})
    if kinds:
        lines.append(f"  evidence: {' · '.join(kinds)}")
    if not scenario.get("ok") and scenario.get("failure"):
        lines.append(f"  failure: {scenario['failure']}")
    lines.append("")
    return lines


def trace_run(run_dir: Path, scenario_filter: str | None = None) -> str:
    """Render the run at `run_dir` as a text timeline.

    `scenario_filter` (substring, case-insensitive) limits which scenarios are shown.
    """
    manifest = _read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        return f"no readable manifest.json in {run_dir}"
    grade = "PASS" if manifest.get("ok") else "FAIL"
    out = [
        f"bajutsu trace · run {manifest.get('runId', '')} · {grade} · driver: {manifest.get('backend', '')}",
        "",
    ]
    provenance = _provenance_by_scenario(run_dir)
    for scenario in manifest.get("scenarios") or []:
        name = str(scenario.get("scenario", ""))
        if scenario_filter and scenario_filter.lower() not in name.lower():
            continue
        out += _scenario_lines(run_dir, scenario, provenance.get(name))
    return "\n".join(out).rstrip() + "\n"

"""M4 self-healing triage — read a failed run, diagnose it, propose a minimal fix.

The boundary holds: triage is **advisory** — it never decides pass/fail, only explains a
failure and suggests an edit a human reviews. `assemble` extracts the failure context from
a saved run (pure); a `TriageAgent` turns that into a diagnosis. The default
`HeuristicTriageAgent` is rule-based (no AI, deterministic — it doubles as the test double);
an AI agent can be dropped in behind the same protocol.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bajutsu.drivers import base
from bajutsu.scenario import Gone, Step, dump_scenarios, load_scenarios

_ACT_TARGETS = ("tap", "double_tap", "long_press", "type", "swipe", "pinch", "rotate")


@dataclass(frozen=True)
class FailedStep:
    index: int
    action: str
    reason: str


@dataclass(frozen=True)
class TriageContext:
    """Everything needed to reason about one failed scenario."""

    scenario: str
    failure: str
    failed_step: FailedStep | None
    failed_expectations: list[str]
    elements: list[base.Element]   # the a11y tree nearest the failure
    scenario_yaml: str             # the failing scenario's definition
    target_id: str | None          # the failing step's selector id, if any
    evidence: list[str] = field(default_factory=list)
    screenshot: bytes | None = None  # the screenshot nearest the failure, if one was captured


@dataclass(frozen=True)
class Triage:
    summary: str
    category: str  # selector | timing | assertion | unknown
    suggestions: list[str]


class TriageAgent(Protocol):
    def triage(self, context: TriageContext) -> Triage: ...


# --- assembly (pure) ---


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _target_id(step: Step) -> str | None:
    """The selector id the failing step acted on (or awaited), if it has one."""
    selectors = [
        step.tap,
        step.double_tap,
        step.long_press.sel if step.long_press else None,
        step.type.into if step.type else None,
        step.swipe.on if step.swipe else None,
        step.pinch.sel if step.pinch else None,
        step.rotate.sel if step.rotate else None,
        step.wait.for_ if step.wait else None,
    ]
    for sel in selectors:
        if sel is not None and sel.id:
            return sel.id
    if step.wait is not None and isinstance(step.wait.until, Gone):
        return step.wait.until.gone.id
    return None


def _nearest_artifact(steps: list[dict[str, Any]], failed_index: int | None, kind: str) -> dict[str, Any] | None:
    """The artifact of `kind` from the failing step, else the nearest earlier step that has one."""
    if failed_index is not None:
        order = [failed_index, *range(failed_index - 1, -1, -1)]
    else:
        order = list(range(len(steps) - 1, -1, -1))
    for i in order:
        if 0 <= i < len(steps):
            for art in steps[i].get("artifacts") or []:
                if isinstance(art, dict) and art.get("kind") == kind:
                    return art
    return None


def _elements_near(run_dir: Path, steps: list[dict[str, Any]], failed_index: int | None) -> list[base.Element]:
    """The element tree from the failing step (or the nearest earlier step that has one)."""
    art = _nearest_artifact(steps, failed_index, "elements")
    if art is not None:
        data = _read_json(run_dir / str(art.get("name")))
        if isinstance(data, list):
            return data
    return []


def _screenshot_near(run_dir: Path, steps: list[dict[str, Any]], failed_index: int | None) -> bytes | None:
    """The screenshot from the failing step (or the nearest earlier step that has one)."""
    art = _nearest_artifact(steps, failed_index, "screenshot")
    if art is not None:
        try:
            return (run_dir / str(art.get("name"))).read_bytes()
        except OSError:
            return None
    return None


def assemble(run_dir: Path, scenario_filter: str | None = None) -> TriageContext | None:
    """Build the triage context for the first failed scenario (matching `scenario_filter`).
    Returns None when the run has no readable manifest or no failed scenario."""
    manifest = _read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        return None
    failed: dict[str, Any] | None = None
    for scenario in manifest.get("scenarios") or []:
        if scenario.get("ok"):
            continue
        if scenario_filter and scenario_filter.lower() not in str(scenario.get("scenario", "")).lower():
            continue
        failed = scenario
        break
    if failed is None:
        return None

    steps = failed.get("steps") or []
    failed_step = None
    for st in steps:
        if not st.get("ok"):
            failed_step = FailedStep(int(st.get("index", -1)), str(st.get("action", "")), str(st.get("reason", "")))
            break
    failed_expectations = [
        str(e.get("detail", "")) + (f" — {e['reason']}" if e.get("reason") else "")
        for e in (failed.get("expect_results") or [])
        if not e.get("ok")
    ]

    name = str(failed.get("scenario", ""))
    scenario_yaml, target_id = "", None
    parsed = _load_scenario(run_dir, name)
    if parsed is not None:
        scenario_yaml = dump_scenarios([parsed])
        if failed_step is not None and 0 <= failed_step.index < len(parsed.steps):
            target_id = _target_id(parsed.steps[failed_step.index])

    return TriageContext(
        scenario=name,
        failure=str(failed.get("failure") or ""),
        failed_step=failed_step,
        failed_expectations=failed_expectations,
        elements=_elements_near(run_dir, steps, failed_step.index if failed_step else None),
        scenario_yaml=scenario_yaml,
        target_id=target_id,
        evidence=sorted({str(a.get("kind")) for a in failed.get("artifacts") or []}),
        screenshot=_screenshot_near(run_dir, steps, failed_step.index if failed_step else None),
    )


def _load_scenario(run_dir: Path, name: str) -> Any:
    try:
        scenarios = load_scenarios((run_dir / "scenario.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return next((s for s in scenarios if s.name == name), None)


# --- the default rule-based agent ---


def _ids(elements: list[base.Element]) -> list[str]:
    return [ident for e in elements if (ident := e.get("identifier"))]


def _close(target: str, elements: list[base.Element]) -> list[str]:
    return difflib.get_close_matches(target, _ids(elements), n=3, cutoff=0.6)


class HeuristicTriageAgent:
    """A deterministic, rule-based triage (no AI): categorize the failure by its shape and
    point at the likely fix — including a "did you mean" when the target id is absent but a
    similar id is on screen (the classic self-heal: an id was renamed)."""

    def triage(self, context: TriageContext) -> Triage:
        fs = context.failed_step
        absent = bool(
            context.target_id and context.elements
            and context.target_id not in _ids(context.elements)
        )
        hints = []
        if absent and context.target_id:
            close = _close(context.target_id, context.elements)
            hints.append(
                f"`{context.target_id}` is not on the captured screen"
                + (f" — did you mean {', '.join('`' + c + '`' for c in close)}?" if close else
                   " (its id may have changed, or the screen differs from expected).")
            )

        if fs is not None and fs.action == "wait":
            sugg = [*hints, "Raise the wait timeout, or check the awaited element/condition is reachable."]
            return Triage("A wait condition was not met before its timeout.", "timing", sugg)

        if fs is not None and fs.action in _ACT_TARGETS:
            if "件一致" in fs.reason and context.target_id:
                sugg = [f"`{context.target_id}` matched multiple elements — add `within` or `index` to disambiguate."]
            else:
                sugg = hints or ["Verify the selector resolves to exactly one element (see the element tree)."]
            return Triage(f"The `{fs.action}` step could not resolve or act on its target.", "selector", sugg)

        if context.failed_expectations:
            sugg = [*hints, "Compare each failed expectation below with the screen state at the end of the run."]
            return Triage("An expectation did not hold.", "assertion", sugg)

        return Triage(
            context.failure or "The scenario failed.",
            "unknown",
            ["Inspect the run with `bajutsu trace` and the captured screenshots / logs."],
        )


# --- rendering ---


def render(context: TriageContext, triage: Triage) -> str:
    lines = [f"triage · {context.scenario}", f"  failure: {context.failure}"]
    if context.failed_step is not None:
        fs = context.failed_step
        lines.append(f"  failed step: [{fs.index}] {fs.action} — {fs.reason}")
    for exp in context.failed_expectations:
        lines.append(f"  failed expect: {exp}")
    if context.evidence:
        lines.append(f"  evidence: {' · '.join(context.evidence)}")
    lines += ["", f"diagnosis [{triage.category}]: {triage.summary}", "suggested fixes:"]
    lines += [f"  - {s}" for s in triage.suggestions]
    return "\n".join(lines)

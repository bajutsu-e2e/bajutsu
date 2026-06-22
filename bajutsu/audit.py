"""Static determinism audit for a scenario — a device-free, AI-free stability score.

The deterministic counterpart to flakiness tolerance (BE-0049): instead of absorbing instability,
this *grades* it. It walks a scenario without a device and scores each selector on the stability
ladder ([selectors.md](../docs/selectors.md)) — a unique `id` beats `label`/`traits`, which beat
`index`/raw coordinates — flags `wait`s gated on an over-loose condition, and flags coordinate
gestures a stable `id` could replace. It is purely observational: no model is consulted, the
scenario is never run, and the verdict / CI gate is never touched.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Gone, Scenario, Step

# `until` conditions that wait for no concrete element / event — best-effort settles, not a
# condition the run can prove was met, so they are a determinism risk worth surfacing.
_LOOSE_UNTIL = {"screenChanged", "settled"}


@dataclass(frozen=True)
class Finding:
    """One determinism risk in a scenario, located and explained for a human to fix."""

    where: str  # the step/assertion the risk is in (e.g. "tap", "expect: value")
    kind: str  # fragile-selector | moderate-selector | coordinate-gesture | loose-wait
    detail: str


@dataclass(frozen=True)
class AuditReport:
    """The per-scenario determinism score, parallel to doctor's id-coverage score."""

    scenario: str
    selectors: int  # selectors graded
    stable: int  # resolve by a unique id (id / idMatches)
    moderate: int  # resolve by label / traits / value (auxiliary, no id)
    fragile: int  # rely on index (the flaky last resort)
    stability: float  # stable / selectors (1.0 when no selectors)
    grade: str  # "Stable" | "Moderate" | "Fragile"
    findings: list[Finding]


def _tier(sel: base.Selector) -> str:
    """Place a selector on the stability ladder by its strongest field."""
    if "index" in sel:  # nth-of-many: a flaky last resort, even alongside an id / idMatches
        return "fragile"
    if "id" in sel or "idMatches" in sel:
        return "stable"
    return "moderate"  # label / labelMatches / traits / value / within


def _with_nested(where: str, sel: base.Selector) -> Iterator[tuple[str, base.Selector]]:
    """Yield a selector and, recursively, any `within` scope it nests (each graded on its own)."""
    yield where, sel
    inner = sel.get("within")
    if isinstance(inner, dict):
        yield from _with_nested(f"{where} within", inner)


def _describe(sel: base.Selector) -> str:
    # The `within` scope is graded separately, so don't repeat its dict in this selector's text.
    return ", ".join(f"{key}={sel[key]!r}" for key in sel if key != "within")  # type: ignore[literal-required]


def _assertion_selectors(a: Assertion) -> Iterator[tuple[str, base.Selector]]:
    """Every UI selector an assertion addresses (request / visual have none)."""
    if a.exists is not None:
        yield "expect: exists", a.exists.sel.as_selector()
    if a.value is not None:
        yield "expect: value", a.value.sel.as_selector()
    if a.label is not None:
        yield "expect: label", a.label.sel.as_selector()
    if a.count is not None:
        yield "expect: count", a.count.sel.as_selector()
    if a.enabled is not None:
        yield "expect: enabled", a.enabled.as_selector()
    if a.disabled is not None:
        yield "expect: disabled", a.disabled.as_selector()
    if a.selected is not None:
        yield "expect: selected", a.selected.as_selector()


def _step_selectors(step: Step) -> Iterator[tuple[str, base.Selector]]:
    """Every selector a step addresses, recursing into control-flow steps.

    Action fields are enumerated by hand (not derived from the Step model), so a new
    selector-bearing action must be added here too, or it escapes the audit.
    """
    if step.tap is not None:
        yield "tap", step.tap.as_selector()
    if step.double_tap is not None:
        yield "doubleTap", step.double_tap.as_selector()
    if step.long_press is not None:
        yield "longPress", step.long_press.sel.as_selector()
    if step.type is not None and step.type.into is not None:
        yield "type", step.type.into.as_selector()
    if step.swipe is not None and step.swipe.on is not None:
        yield "swipe", step.swipe.on.as_selector()
    if step.pinch is not None:
        yield "pinch", step.pinch.sel.as_selector()
    if step.rotate is not None:
        yield "rotate", step.rotate.sel.as_selector()
    if step.wait is not None:
        if step.wait.for_ is not None:
            yield "wait", step.wait.for_.as_selector()
        elif isinstance(step.wait.until, Gone):
            yield "wait", step.wait.until.gone.as_selector()
    if step.extract is not None:
        for ex in step.extract.values():
            yield "extract", ex.sel.as_selector()
    for a in step.assert_ or []:
        yield from _assertion_selectors(a)
    if step.if_ is not None:
        yield from _assertion_selectors(step.if_.condition)
        for nested in (*step.if_.then, *(step.if_.else_ or [])):
            yield from _step_selectors(nested)
    if step.for_each is not None:
        yield "forEach", step.for_each.sel.as_selector()
        for nested in step.for_each.steps:
            yield from _step_selectors(nested)


def referenced_ids(scenario: Scenario) -> set[str]:
    """The stable ids (`id` / `idMatches`) a scenario statically references — across steps, nested
    control flow, `within` scopes, and assertions. The coverage map (BE-0050) measures these
    against an app's declared `idNamespaces`. Pure: no device, no model, no side effects."""
    addressed = [
        *(ws for step in scenario.steps for ws in _step_selectors(step)),
        *(ks for a in scenario.expect for ks in _assertion_selectors(a)),
    ]
    ids: set[str] = set()
    for where, top in addressed:
        for _, sel in _with_nested(where, top):
            if "id" in sel:
                ids.add(sel["id"])
            if "idMatches" in sel:
                ids.add(sel["idMatches"])
    return ids


def _step_findings(step: Step) -> Iterator[Finding]:
    """Non-selector determinism risks in a step (coordinate gestures, over-loose waits)."""
    if step.swipe is not None and step.swipe.from_ is not None:
        yield Finding(
            "swipe",
            "coordinate-gesture",
            "swipes by raw coordinates ({from,to}); prefer {on,direction} on a stable id",
        )
    if (
        step.wait is not None
        and isinstance(step.wait.until, str)
        and step.wait.until in _LOOSE_UNTIL
    ):
        yield Finding(
            "wait",
            "loose-wait",
            f"waits on {step.wait.until!r} (no concrete element/condition); prefer wait `for` an id",
        )
    if step.if_ is not None:
        for nested in (*step.if_.then, *(step.if_.else_ or [])):
            yield from _step_findings(nested)
    if step.for_each is not None:
        for nested in step.for_each.steps:
            yield from _step_findings(nested)


def _selector_finding(where: str, sel: base.Selector, tier: str) -> Finding:
    if tier == "fragile":
        return Finding(
            where, "fragile-selector", f"{_describe(sel)} relies on index (a flaky last resort)"
        )
    return Finding(where, "moderate-selector", f"{_describe(sel)} is auxiliary; prefer a unique id")


def audit_scenario(scenario: Scenario) -> AuditReport:
    """Grade one scenario's determinism, statically. Pure: no device, no model, no side effects."""
    addressed = [
        *(ws for step in scenario.steps for ws in _step_selectors(step)),
        *(ks for a in scenario.expect for ks in _assertion_selectors(a)),
    ]
    # A `within` scope is itself resolved at runtime, so grade nested selectors too — a fragile
    # `within` (e.g. by index) is a determinism risk that would otherwise slip through.
    located = [exp for where, sel in addressed for exp in _with_nested(where, sel)]
    # Grade each selector once, then derive both the counts and the findings from that.
    graded = [(where, sel, _tier(sel)) for where, sel in located]
    tiers = Counter(tier for _, _, tier in graded)
    gesture_findings = [f for step in scenario.steps for f in _step_findings(step)]
    findings = [
        *(_selector_finding(w, sel, tier) for w, sel, tier in graded if tier != "stable"),
        *gesture_findings,
    ]
    total = len(located)
    grade = _grade(tiers, gesture_findings)
    return AuditReport(
        scenario=scenario.name,
        selectors=total,
        stable=tiers["stable"],
        moderate=tiers["moderate"],
        fragile=tiers["fragile"],
        stability=tiers["stable"] / total if total else 1.0,
        grade=grade,
        findings=findings,
    )


def _grade(tiers: Counter[str], gesture_findings: list[Finding]) -> str:
    if tiers["fragile"] or any(f.kind == "coordinate-gesture" for f in gesture_findings):
        return "Fragile"
    if tiers["moderate"] or any(f.kind == "loose-wait" for f in gesture_findings):
        return "Moderate"
    return "Stable"


def render(report: AuditReport) -> str:
    """Human-readable summary that points at what to harden."""
    stability = (
        "stability: n/a (no selectors)"
        if report.selectors == 0
        else f"stability: {report.stability:.2f} "
        f"({report.stable}/{report.selectors} selectors id-based)"
    )
    lines = [f"scenario: {report.scenario}", f"grade: {report.grade}", stability]
    lines.extend(f"  {f.where}: {f.detail}" for f in report.findings)
    return "\n".join(lines)

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
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from bajutsu import device_os
from bajutsu.common.scenario import Assertion, Gone, Scenario, Step
from bajutsu.device_os import DeviceOS
from bajutsu.drivers import base

if TYPE_CHECKING:
    from bajutsu.common.orchestrator import RunResult

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
    if step.drag is not None:
        yield "drag", step.drag.on.as_selector()
    if step.scroll is not None:
        yield "scroll", step.scroll.to.as_selector()
        if step.scroll.within is not None:
            yield "scroll", step.scroll.within.as_selector()
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


def _located_selectors(scenario: Scenario) -> Iterator[tuple[str, base.Selector]]:
    """Every selector the scenario addresses, each followed by the selectors nested in its `within` scope.

    Covers steps and scenario-level assertions. The single lazy walk both the determinism audit and
    the coverage map (BE-0050) consume, so a new selector source is added in one place and the two
    never diverge.
    """
    for step in scenario.steps:
        for where, sel in _step_selectors(step):
            yield from _with_nested(where, sel)
    for a in scenario.expect:
        for where, sel in _assertion_selectors(a):
            yield from _with_nested(where, sel)


def _selector_ids(sel: base.Selector) -> Iterator[str]:
    """The stable id(s) one selector references (`id` / `idMatches`).

    `id` / `idMatches` may each be a list of OR candidates (BE-0221) — the same logical id in each
    platform's spelling. Consumers bucket by `namespace_of` (splits on `.`), so only the primary
    (SPEC, dotted) candidate is the referenced id; a platform's underscore alternate (`stable_row_1`)
    has no `.` and would otherwise register as a spurious off-namespace id.
    """
    if "id" in sel:
        yield base.id_candidates(sel["id"])[0]
    if "idMatches" in sel:
        yield base.id_candidates(sel["idMatches"])[0]


def _selector_match_ids(sel: base.Selector) -> Iterator[str]:
    """Every literal id spelling one selector references — **all** `id` OR-candidates, no `idMatches`.

    The string-matching counterpart of `_selector_ids`, for test impact analysis (BE-0321), which
    substring-matches these against app source rather than bucketing by `namespace_of`. Two differences
    follow from that: every BE-0221 candidate is yielded, not just the canonical dotted one, because a
    change to any platform's spelling (`login.button` *or* `login_button`) must be matched; and
    `idMatches` is dropped, because a regex is a pattern, not a literal that appears verbatim in source
    (mirroring the endpoint side dropping `urlMatches` / `pathMatches`).
    """
    if "id" in sel:
        yield from base.id_candidates(sel["id"])


def step_matchable_ids(step: Step) -> set[str]:
    """Every literal id spelling one step references (all candidates, no `idMatches`), for BE-0321.

    Covers the step's nested control flow and `within` scopes. Impact analysis inverts these into a
    literal → step index and substring-matches each against a diff. Pure.
    """
    return {
        rid
        for where, sel in _step_selectors(step)
        for _, scoped in _with_nested(where, sel)
        for rid in _selector_match_ids(scoped)
    }


def scenario_matchable_ids(scenario: Scenario) -> set[str]:
    """Every literal id spelling a scenario references (all candidates, no `idMatches`), for BE-0321.

    The scenario-wide counterpart of `step_matchable_ids`, covering steps, nested control flow,
    `within` scopes, and scenario-level assertions. Pure.
    """
    return {rid for _, sel in _located_selectors(scenario) for rid in _selector_match_ids(sel)}


def referenced_ids(scenario: Scenario) -> set[str]:
    """The stable ids (`id` / `idMatches`) a scenario statically references.

    Covers steps, nested control flow, `within` scopes, and assertions. The coverage map (BE-0050)
    measures these against an app's declared `idNamespaces`. Pure: no device, no model, no side
    effects.
    """
    return {rid for _, sel in _located_selectors(scenario) for rid in _selector_ids(sel)}


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
    # A `within` scope is itself resolved at runtime, so grade nested selectors too — a fragile
    # `within` (e.g. by index) is a determinism risk that would otherwise slip through. The walk
    # (steps + assertions, each with its nested scopes) is shared with the coverage map.
    located = list(_located_selectors(scenario))
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


# --- repeat-and-diff: prove determinism dynamically (BE-0049) ---
#
# Run a scenario K times under identical preconditions and report anything that varies as a
# *finding to fix*. The audit never changes a verdict and never feeds the run/CI gate.


@dataclass(frozen=True)
class RepeatReport:
    """The verdict of running one scenario K times and diffing the outcomes."""

    scenario: str
    runs: int  # K — how many times it was executed
    deterministic: bool  # every run agreed (or K < 2, nothing to compare)
    divergences: list[str] = field(default_factory=list)  # what varied, for a human to fix


def _verdicts(oks: list[bool]) -> list[str]:
    return ["pass" if o else "fail" for o in oks]


def repeat_diff(results: list[RunResult]) -> RepeatReport:
    """Classify K runs of one scenario as deterministic or flaky by diffing their outcomes.

    Compares the per-step pass/fail, per-assertion pass/fail, and the overall verdict across runs;
    any variation is a divergence. With fewer than two runs there is nothing to compare, so the
    result is trivially deterministic (unproven, not flaky).
    """
    scenario = results[0].scenario if results else ""
    if len(results) < 2:
        return RepeatReport(scenario, len(results), deterministic=True)

    def signature(r: RunResult) -> object:
        return (
            r.ok,
            tuple(
                (s.index, s.action, s.ok, tuple((a.ok, a.kind) for a in s.assertion_results))
                for s in r.steps
            ),
            tuple((a.ok, a.kind) for a in r.expect_results),
        )

    if all(signature(r) == signature(results[0]) for r in results):
        return RepeatReport(scenario, len(results), deterministic=True)

    divergences: list[str] = []
    if len({r.ok for r in results}) > 1:
        divergences.append(f"overall verdict varied: {_verdicts([r.ok for r in results])}")
    if len({len(r.steps) for r in results}) > 1:
        counts = sorted({len(r.steps) for r in results})
        divergences.append(f"step count varied across runs: {counts}")

    # Compare step- and assertion-level pass/fail over the common prefix (a varying step count is
    # already reported above; here we surface *which* shared step or assertion flips).
    common = min(len(r.steps) for r in results)
    for i in range(common):
        if len({r.steps[i].ok for r in results}) > 1:
            action = results[0].steps[i].action
            divergences.append(
                f"step {i} ({action}) verdict varied: {_verdicts([r.steps[i].ok for r in results])}"
            )
        acounts = {len(r.steps[i].assertion_results) for r in results}
        if len(acounts) == 1:
            for j in range(next(iter(acounts))):
                aoks = [r.steps[i].assertion_results[j].ok for r in results]
                if len(set(aoks)) > 1:
                    kind = results[0].steps[i].assertion_results[j].kind
                    divergences.append(f"step {i} assertion {j} ({kind}) varied: {_verdicts(aoks)}")
        else:
            divergences.append(f"step {i} assertion count varied across runs: {sorted(acounts)}")

    ecounts = {len(r.expect_results) for r in results}
    if len(ecounts) == 1:
        for j in range(next(iter(ecounts))):
            eoks = [r.expect_results[j].ok for r in results]
            if len(set(eoks)) > 1:
                kind = results[0].expect_results[j].kind
                divergences.append(f"expect {j} ({kind}) varied: {_verdicts(eoks)}")
    else:
        divergences.append(f"expect-assertion count varied across runs: {sorted(ecounts)}")

    # The signatures differed but none of the above pinpointed it — a step's action name or an
    # assertion kind changed between runs while every verdict and count matched. Still flaky, so
    # report it rather than swallow it. (repeat_diff is pure — there is no evidence to point at.)
    if not divergences:
        divergences.append(
            "run outcomes differed across repeats (a step action or assertion kind changed)"
        )
    return RepeatReport(scenario, len(results), deterministic=False, divergences=divergences)


def render_repeat(report: RepeatReport) -> str:
    """Human-readable repeat-and-diff verdict, pointing at what varied."""
    classification = "deterministic" if report.deterministic else "flaky"
    lines = [f"scenario: {report.scenario}", f"{report.runs} runs: {classification}"]
    lines.extend(f"  {d}" for d in report.divergences)
    return "\n".join(lines)


@dataclass(frozen=True)
class ScenarioHistory:
    """One scenario's verdict history at a fixed fingerprint *on one OS* — the longitudinal unit."""

    scenario_hash: (
        str  # the run's `provenance.scenarioHash` — the executed file's content fingerprint
    )
    name: str  # the scenario whose outcomes these are (the manifest's per-scenario `scenario`)
    # The OS these runs ran on, parsed from the per-scenario `device_runtime` (BE-0358); None when
    # no run named one. Part of the identity, so a verdict that differs *because the OS differs* is
    # two histories rather than one flaky one.
    device_os: DeviceOS | None
    runs: int  # how many accumulated runs exercised this scenario at this fingerprint
    passed: int  # runs in which it passed
    failed: int  # runs in which it failed
    pass_rate: float  # passed / runs
    classification: str  # flaky | deterministic | unproven (see `classify_stability`)


@dataclass(frozen=True)
class LongitudinalReport:
    """Flakiness mined from accumulated run history — each scenario's verdict over its own past."""

    histories: list[
        ScenarioHistory
    ]  # one per (fingerprint, scenario, OS), flaky first then by run count
    skipped: int  # runs with no `scenarioHash` provenance — can't be grouped by identity


def longitudinal(manifests: Iterable[Mapping[str, object]]) -> LongitudinalReport:
    """Group accumulated runs by scenario fingerprint and classify each scenario's stability (BE-0049).

    The longitudinal half of the determinism audit. A run stamps one `provenance.scenarioHash` (the
    executed file's content) over its whole `scenarios` list, so each scenario's outcomes are keyed by
    that fingerprint *and* the scenario's name: a verdict that flips at a constant fingerprint is true
    flakiness, while an edited scenario gets a new fingerprint and a fresh group (an edit can't look
    like a flake). The key also carries the parsed device OS (BE-0358), so two runs of one scenario on
    two OS versions form two histories, each classified on its own evidence — a reproducible OS
    difference is a finding about that OS, not a flake. Pure and observational — it reads the identity
    stamp and the recorded per-scenario verdict only, never deciding or changing a verdict.

    Args:
        manifests: Parsed `manifest.json` mappings, in any order. A manifest with no
            `provenance.scenarioHash` (a pre-provenance run) can't be grouped and is counted in
            `skipped` instead of contributing history. A scenario entry whose `device_runtime` is
            missing or unrecognized groups under a distinct unknown-OS key: merging it into a
            version's history would reintroduce the blending this grouping removes, and dropping it
            would lose a run the audit can still classify.

    Returns:
        The per-(fingerprint, scenario, OS) histories, flaky first then by descending run count, plus
        the count of runs skipped for lacking a fingerprint.
    """
    groups: dict[tuple[str, str, DeviceOS | None], list[bool]] = {}
    skipped = 0
    for m in manifests:
        prov = m.get("provenance")
        scenario_hash = prov.get("scenarioHash") if isinstance(prov, dict) else None
        if not isinstance(scenario_hash, str):
            skipped += 1
            continue
        scenarios = m.get("scenarios")
        for s in scenarios if isinstance(scenarios, list) else []:
            name = s.get("scenario") if isinstance(s, dict) else None
            if isinstance(name, str) and name:
                key = (scenario_hash, name, device_os.parse(s.get("device_runtime")))
                groups.setdefault(key, []).append(bool(s.get("ok")))

    histories = [_history(h, name, os, oks) for (h, name, os), oks in groups.items()]
    # Flaky first (the findings to act on), then the most-observed scenarios — both descending. The
    # OS sits ahead of the name so two histories that differ only by it order deterministically
    # rather than by input order.
    histories.sort(
        key=lambda h: (
            h.classification != "flaky",
            -h.runs,
            h.scenario_hash,
            device_os.ordering_key(h.device_os),
            h.name,
        )
    )
    return LongitudinalReport(histories=histories, skipped=skipped)


def classify_stability(passed: int, runs: int) -> str:
    """Classify a scenario's verdict history as `flaky` / `deterministic` / `unproven`.

    The single classification rule shared by the longitudinal audit and the DB-backed cross-run
    flakiness score (BE-0220), so both label identically. A single run proves nothing (mirrors
    repeat-and-diff with K<2): `unproven`, not flaky. With two or more, a mix of pass and fail at
    the *same* fingerprint is true flakiness; an all-pass or all-fail history is `deterministic` (a
    consistent failure is reproducible, not flaky).

    Args:
        passed: Runs in which the scenario passed.
        runs: Total runs observed at one content fingerprint.
    """
    if runs < 2:
        return "unproven"
    if passed and passed < runs:
        return "flaky"
    return "deterministic"


def _history(
    scenario_hash: str, name: str, os: DeviceOS | None, oks: list[bool]
) -> ScenarioHistory:
    """Tally one scenario's verdicts at a fingerprint, on one OS, into a classified history."""
    passed = sum(oks)
    runs = len(oks)
    return ScenarioHistory(
        scenario_hash=scenario_hash,
        name=name,
        device_os=canonical_os(os),
        runs=runs,
        passed=passed,
        failed=runs - passed,
        pass_rate=passed / runs,
        classification=classify_stability(passed, runs),
    )


def canonical_os(os: DeviceOS | None) -> DeviceOS | None:
    """The parsed OS with its raw label replaced by the canonical spelling, for a report entry.

    A group can hold several spellings of one version (`iOS 18.6` and `iOS 18.6.1` are the same OS),
    and the one that survives into the group key is whichever run was read first. Emitting that raw
    label would make `--json` depend on input order for a history that is otherwise identical — the
    same order-dependence the OS component of the sort keys exists to remove.
    """
    return replace(os, label=os.display) if os is not None else None


def unknown_os_note(unknown: int) -> str:
    """The disclosure line both flakiness surfaces print when *unknown* histories have no recorded OS.

    Shared so the file-backed audit and the hosted ranking word it identically (BE-0358). Without it,
    a deployment that started recording the OS mid-history would show its older runs split off under
    the unknown OS with nothing saying why, and a genuine flake spanning that boundary would read as
    two `unproven` histories rather than as one finding. "No *single* OS" rather than "no OS": a run
    whose scenarios spanned two versions lands here too, and did record one per scenario.
    """
    subject = "history" if unknown == 1 else "histories"
    return (
        f"{unknown} {subject} with no single recorded device OS, grouped under "
        f"{device_os.describe(None)} separately from the per-OS ones"
    )


def render_longitudinal(report: LongitudinalReport) -> str:
    """Human-readable longitudinal view: each scenario's OS, classification, and pass rate."""
    if not report.histories:
        body = ["no runs with a scenario fingerprint to analyze"]
    else:
        body = [
            f"{h.name} on {device_os.describe(h.device_os)}: {h.classification} "
            f"({h.passed}/{h.runs} passed, {h.pass_rate:.0%})"
            for h in report.histories
        ]
    if unknown := sum(1 for h in report.histories if h.device_os is None):
        body.append(unknown_os_note(unknown))
    if report.skipped:
        body.append(f"skipped {report.skipped} run(s) with no scenario fingerprint")
    return "\n".join(body)

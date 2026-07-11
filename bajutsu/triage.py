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
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from bajutsu.drivers import base
from bajutsu.scenario import (
    Assertion,
    Gone,
    Selector,
    Step,
    TextMatch,
    Wait,
    dump_scenarios,
    load_scenarios,
)

_ACT_TARGETS = ("tap", "double_tap", "long_press", "type", "swipe", "pinch", "rotate")


@dataclass(frozen=True)
class FailedStep:
    """The step that failed — its index, action, and failure reason."""

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
    elements: list[base.Element]  # the a11y tree nearest the failure
    scenario_yaml: str  # the failing scenario's definition
    target_id: str | None  # the failing step's selector id, if any
    evidence: list[str] = field(default_factory=list)
    screenshot: bytes | None = None  # the screenshot nearest the failure, if one was captured


@dataclass(frozen=True)
class RunEvidence:
    """One run's evidence for cross-run flaky triage — its verdict and the state nearest its end.

    For a failing run the state is captured nearest the failed step; for a passing run there is no
    failure, so it is the element tree / screenshot captured at the run's end.
    """

    run_id: str
    ok: bool
    failure: str
    failed_step: FailedStep | None
    failed_expectations: list[str]
    elements: list[base.Element]  # a11y tree nearest the failure (failing) or run end (passing)
    screenshot: bytes | None = None


@dataclass(frozen=True)
class CrossRunTriageContext:
    """The delta material to reason about why one scenario intermittently passes and fails (BE-0220).

    Unlike `TriageContext` (one failed run), this gathers the same scenario's evidence across
    several of its passing and failing runs at a fixed content fingerprint, so an investigator can
    reason about what *varies* between a pass and a fail — the cross-run counterpart to per-failure
    triage.
    """

    scenario: str
    scenario_hash: str | None  # the runs' shared fingerprint, when known (the grouping key)
    scenario_yaml: str  # the scenario's definition (shared across the runs at one fingerprint)
    target_id: str | None  # the failing step's selector id, if any
    passing: list[RunEvidence]
    failing: list[RunEvidence]


FIX_KINDS = ("renameId", "addIndex", "raiseTimeout")
_FIX_LABELS = {
    "renameId": "rename id",
    "addIndex": "disambiguate selector",
    "raiseTimeout": "raise timeout",
}


def fix_summary(kind: str, find: str, replace: str) -> str:
    """A human-readable one-line label for a fix: the kind's label plus find -> replace."""
    return f"{_FIX_LABELS.get(kind, kind)} `{find}` -> `{replace}`"


@dataclass(frozen=True)
class Fix:
    """A mechanically-applicable edit a human reviews before it is written (`find` -> `replace`).

    Applied over the scenario source.
    `renameId` replaces a selector id as a whole token (safe to apply everywhere it appears —
    the classic self-heal). `addIndex` / `raiseTimeout` replace an exact fragment of the
    failing step (disambiguate an ambiguous match, or lengthen a wait). The boundary still
    holds: every fix is shown as a diff and written only when the human opts in, and a fragment
    that no longer matches the source is a safe no-op.
    """

    kind: str  # one of FIX_KINDS
    summary: str
    find: str
    replace: str


@dataclass(frozen=True)
class Triage:
    """The triage verdict for one failed scenario — a summary, a category, and suggested fixes."""

    summary: str
    category: str  # selector | timing | assertion | unknown
    suggestions: list[str]
    fix: Fix | None = None


class TriageAgent(Protocol):
    """The triage interface: turn a failed scenario's context into a `Triage` verdict.

    Implemented by the deterministic `HeuristicTriageAgent` (no AI) and by AI-backed agents alike.
    """

    def triage(self, context: TriageContext) -> Triage: ...


class CrossRunTriageAgent(Protocol):
    """The cross-run interface: diagnose why one scenario intermittently flips at a fixed fingerprint.

    The single-run `TriageAgent` reasons about one failure; this reasons about the delta between
    passing and failing runs of the same definition. AI-only — there is no deterministic
    implementation, since spotting the discriminating difference is exactly the judgement an LLM adds.
    """

    def triage_flaky(self, context: CrossRunTriageContext) -> Triage: ...


# --- applying a fix (pure) ---


def apply_fix(text: str, fix: Fix) -> tuple[str, int]:
    """Apply `fix` to scenario source `text`; return (patched_text, replacement_count).

    `renameId` replaces whole-token occurrences of the id — the negative lookarounds keep
    `nav.setting` from matching inside `nav.settings`. The fragment kinds (`addIndex`,
    `raiseTimeout`) replace an exact substring; when it no longer matches the source the count
    is 0 and the text is unchanged — a safe no-op the diff makes obvious.
    """
    if not fix.find:
        return text, 0
    if fix.kind == "renameId":
        pattern = re.compile(r"(?<![\w.])" + re.escape(fix.find) + r"(?![\w.])")
        return pattern.subn(fix.replace, text)
    return text.replace(fix.find, fix.replace), text.count(fix.find)


def diff_fix(old: str, new: str, path: str) -> str:
    """A unified diff of a fix, for the human to review before `--write`."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )


@dataclass(frozen=True)
class AppliedFix:
    """A fix applied to scenario source, packaged for a UI to preview and write back (BE-0147).

    `patched` is the full source with the fix applied; `diff` is its unified diff — empty when
    `count` is 0 (the fragment no longer matches the source, a safe no-op the diff makes obvious).
    """

    path: str
    count: int
    diff: str
    patched: str


def apply_result(source: str, path: str, fix: Fix) -> AppliedFix:
    """Apply `fix` to `source`, packaging the patched text and unified diff for a UI (BE-0147).

    The write itself stays the human's explicit action — this only prepares the preview.
    """
    patched, count = apply_fix(source, fix)
    return AppliedFix(path, count, diff_fix(source, patched, path) if count else "", patched)


# --- laxer guard (BE-0023) ---


@dataclass(frozen=True)
class _LaxMetrics:
    """The check-strength of a scenario, reduced to counts so before/after are comparable."""

    assertions: int  # every machine check (scenario `expect` + each step `assert`)
    equals_matchers: int  # value/label matchers pinned to `equals` — the tightest kind
    id_selectors: int  # selectors anchored on an `id`, the uniqueness anchor
    waits: int  # bounded condition waits
    wait_timeout_total: float  # summed wait budget; a raise grows it, a lowering shrinks it


def _iter_models(node: Any) -> Iterator[BaseModel]:
    """Every pydantic model anywhere in a scenario tree — the shape-agnostic way to count checks."""
    if isinstance(node, BaseModel):
        yield node
        for value in node.__dict__.values():
            yield from _iter_models(value)
    elif isinstance(node, list | tuple):
        for item in node:
            yield from _iter_models(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from _iter_models(item)


def _measure(scenarios: list[Any]) -> _LaxMetrics:
    models = [m for scenario in scenarios for m in _iter_models(scenario)]
    waits = [m for m in models if isinstance(m, Wait)]
    return _LaxMetrics(
        assertions=sum(isinstance(m, Assertion) for m in models),
        equals_matchers=sum(isinstance(m, TextMatch) and m.equals is not None for m in models),
        id_selectors=sum(isinstance(m, Selector) and m.id is not None for m in models),
        waits=len(waits),
        wait_timeout_total=sum(w.timeout for w in waits),
    )


def flag_laxer(scenario_yaml: str, fix: Fix | None) -> list[str]:
    """Warn when applying `fix` would weaken what the scenario checks (BE-0023, advisory only).

    Compares the scenario's check-strength before and after the fix — a structural before/after
    over the parsed models, not the fix's declared kind — so a proposal that removes an assertion,
    loosens a value/label match, widens a selector past its id, or drops / lowers a wait timeout is
    surfaced to the reviewer instead of quietly reducing coverage to "make it pass". Never a verdict:
    a flagged fix is still shown as a diff a human decides on.

    Returns:
        One human-readable line per way the fix relaxes the test; empty when it does not (a no-op
        fix, a benign rename, or a raised timeout). A patch that no longer parses can't be analyzed,
        so that too is reported rather than passed off as safe.
    """
    if fix is None:
        return []
    patched, count = apply_fix(scenario_yaml, fix)
    if count == 0:
        return []  # the fragment no longer matches — a safe no-op, nothing to weaken
    try:
        before = _measure(load_scenarios(scenario_yaml))
    except Exception:  # an unparseable baseline leaves nothing to compare against
        return []
    try:
        after = _measure(load_scenarios(patched))
    except Exception:  # advisory guard must never crash the triage path
        return [
            "proposal could not be parsed as a scenario, so it could not be analyzed for laxer changes"
        ]

    warnings: list[str] = []
    if after.assertions < before.assertions:
        dropped = before.assertions - after.assertions
        warnings.append(f"removes {dropped} assertion(s) — the test would check less")
    if after.waits < before.waits:
        warnings.append("drops a wait — a timing guard is removed")
    elif after.wait_timeout_total < before.wait_timeout_total:
        warnings.append("lowers a wait timeout — could mask slower behavior")
    # Selector / matcher relaxations only count when no check was removed outright, so a removed
    # assertion's own selectors and matchers aren't double-reported as a widening or loosening.
    if after.assertions == before.assertions:
        if after.equals_matchers < before.equals_matchers:
            warnings.append(
                "loosens a value/label match (equals -> broader) — more inputs would pass"
            )
        if after.waits == before.waits and after.id_selectors < before.id_selectors:
            warnings.append("widens a selector past its id — could match more than one element")
    return warnings


def result_payload(
    context: TriageContext, triage: Triage, applied: AppliedFix | None = None
) -> dict[str, Any]:
    """A JSON-serializable triage result for the serve Web UI (BE-0147).

    Mirrors the terminal `render`, plus — when a structured fix was applied against the scenario
    source — the unified diff and patched text the UI previews and writes back through the
    validated scenario-save path, and the BE-0023 laxer warnings for that fix. Never a verdict: the
    run already decided pass/fail; this only explains and proposes.
    """
    fs, fix = context.failed_step, triage.fix
    # These three are flat frozen dataclasses of JSON-safe fields, so `asdict` gives the payload
    # its keys — no hand-listed dict to drift when a field is added. (TriageContext itself can't:
    # its `elements`/`screenshot` aren't JSON-serializable, so it's projected field by field.)
    return {
        "scenario": context.scenario,
        "failure": context.failure,
        "failedStep": asdict(fs) if fs is not None else None,
        "failedExpectations": list(context.failed_expectations),
        "evidence": list(context.evidence),
        "category": triage.category,
        "summary": triage.summary,
        "suggestions": list(triage.suggestions),
        "fix": asdict(fix) if fix is not None else None,
        "apply": asdict(applied) if applied is not None else None,
        "laxer": flag_laxer(context.scenario_yaml, fix),
    }


# --- cross-run flaky triage surface (BE-0220 Half 2) ---


def render_cross_run(context: CrossRunTriageContext, triage: Triage) -> str:
    """Render a cross-run flaky triage as a text report (advisory — never the pass/fail judge).

    Contrasts the scenario's passing and failing runs, then shows the proposal as a reviewable diff
    against the current scenario (never applied here) with any BE-0023 laxer warnings inline, so a
    reviewer sees a weaker check before accepting it.
    """
    lines = [f"flaky triage · {context.scenario}"]
    if context.scenario_hash:
        lines.append(f"  fingerprint: {context.scenario_hash}")
    lines.append(f"  runs: {len(context.passing)} passing · {len(context.failing)} failing")
    if context.target_id:
        lines.append(f"  flaky step id: {context.target_id}")
    lines += ["", f"diagnosis [{triage.category}]: {triage.summary}", "suggested fixes:"]
    lines += [f"  - {s}" for s in triage.suggestions]
    fix = triage.fix
    if fix is not None:
        patched, count = apply_fix(context.scenario_yaml, fix)
        lines += ["", f"proposed fix: {fix.summary}"]
        if count:
            lines.append(diff_fix(context.scenario_yaml, patched, context.scenario))
        else:
            lines.append(f"  (`{fix.find}` not found in the scenario — no-op)")
        warnings = flag_laxer(context.scenario_yaml, fix)
        if warnings:
            lines.append("laxer-guard warnings (BE-0023):")
            lines += [f"  ! {w}" for w in warnings]
    return "\n".join(lines)


def _evidence_summary(evidence: RunEvidence) -> dict[str, Any]:
    """One run's verdict and failure, projected to JSON-safe fields (the tree/screenshot are not)."""
    return {
        "runId": evidence.run_id,
        "ok": evidence.ok,
        "failure": evidence.failure,
        "failedStep": asdict(evidence.failed_step) if evidence.failed_step is not None else None,
        "failedExpectations": list(evidence.failed_expectations),
    }


def cross_run_payload(
    context: CrossRunTriageContext, triage: Triage, applied: AppliedFix | None = None
) -> dict[str, Any]:
    """A JSON-serializable cross-run triage result for CI / scripting (mirrors `render_cross_run`).

    Carries the run verdicts (so a consumer can link to each run's evidence), the diagnosis, the
    proposed fix and — when applied against a scenario source — its diff, plus the BE-0023 laxer
    warnings. Never a verdict: the runs already decided pass/fail; this only explains and proposes.
    """
    fix = triage.fix
    return {
        "scenario": context.scenario,
        "scenarioHash": context.scenario_hash,
        "targetId": context.target_id,
        "passing": [_evidence_summary(ev) for ev in context.passing],
        "failing": [_evidence_summary(ev) for ev in context.failing],
        "category": triage.category,
        "summary": triage.summary,
        "suggestions": list(triage.suggestions),
        "fix": asdict(fix) if fix is not None else None,
        "apply": asdict(applied) if applied is not None else None,
        "laxer": flag_laxer(context.scenario_yaml, fix),
    }


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


def _nearest_artifact(
    steps: list[dict[str, Any]], failed_index: int | None, kind: str
) -> dict[str, Any] | None:
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


def _read_artifact[T](
    run_dir: Path,
    steps: list[dict[str, Any]],
    failed_index: int | None,
    kind: str,
    loader: Callable[[Path], T | None],
    default: T,
) -> T:
    """Find the nearest artifact of `kind` (backward from `failed_index`) and apply `loader`.

    Separates the backward-scan concern (_nearest_artifact) from the per-type deserialization
    so callers only declare what they want, not how to walk the step list.
    """
    art = _nearest_artifact(steps, failed_index, kind)
    if art is not None:
        result = loader(run_dir / str(art.get("name")))
        if result is not None:
            return result
    return default


def _load_elements(path: Path) -> list[base.Element] | None:
    data = _read_json(path)
    return data if isinstance(data, list) else None


def _load_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _elements_near(
    run_dir: Path, steps: list[dict[str, Any]], failed_index: int | None
) -> list[base.Element]:
    """The element tree from the failing step (or the nearest earlier step that has one)."""
    return _read_artifact(run_dir, steps, failed_index, "elements", _load_elements, [])


def _screenshot_near(
    run_dir: Path, steps: list[dict[str, Any]], failed_index: int | None
) -> bytes | None:
    """The screenshot from the failing step (or the nearest earlier step that has one)."""
    return _read_artifact(run_dir, steps, failed_index, "screenshot", _load_bytes, None)


def assemble(run_dir: Path, scenario_filter: str | None = None) -> TriageContext | None:
    """Build the triage context for the first failed scenario (matching `scenario_filter`).

    Returns None when the run has no readable manifest or no failed scenario.
    """
    manifest = _read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        return None
    failed: dict[str, Any] | None = None
    for scenario in manifest.get("scenarios") or []:
        if scenario.get("ok"):
            continue
        if (
            scenario_filter
            and scenario_filter.lower() not in str(scenario.get("scenario", "")).lower()
        ):
            continue
        failed = scenario
        break
    if failed is None:
        return None

    steps = failed.get("steps") or []
    failed_step = _first_failed_step(steps)
    failed_expectations = _failed_expectations(failed)

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


def _first_failed_step(steps: list[dict[str, Any]]) -> FailedStep | None:
    """The first step that did not pass, as a `FailedStep` — None when every step passed."""
    for st in steps:
        if not st.get("ok"):
            return FailedStep(
                int(st.get("index", -1)), str(st.get("action", "")), str(st.get("reason", ""))
            )
    return None


def _failed_expectations(scenario: dict[str, Any]) -> list[str]:
    """The detail (and reason) of each expectation that did not hold in `scenario`."""
    return [
        str(e.get("detail", "")) + (f" — {e['reason']}" if e.get("reason") else "")
        for e in (scenario.get("expect_results") or [])
        if not e.get("ok")
    ]


def _load_scenario(run_dir: Path, name: str) -> Any:
    try:
        scenarios = load_scenarios((run_dir / "scenario.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return next((s for s in scenarios if s.name == name), None)


# --- cross-run assembly (BE-0220 Half 2, pure) ---


def _run_evidence(run_dir: Path, scenario: str) -> RunEvidence | None:
    """The evidence one run holds for `scenario`: its verdict and the state nearest its end/failure.

    Returns None when the run has no readable manifest or does not include a scenario of that name.
    """
    manifest = _read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        return None
    match = next(
        (s for s in manifest.get("scenarios") or [] if str(s.get("scenario", "")) == scenario),
        None,
    )
    if match is None:
        return None
    steps = match.get("steps") or []
    failed_step = _first_failed_step(steps)
    index = failed_step.index if failed_step else None
    return RunEvidence(
        run_id=str(manifest.get("runId") or run_dir.name),
        ok=bool(match.get("ok")),
        failure=str(match.get("failure") or ""),
        failed_step=failed_step,
        failed_expectations=_failed_expectations(match),
        elements=_elements_near(run_dir, steps, index),
        screenshot=_screenshot_near(run_dir, steps, index),
    )


def assemble_cross_run(
    pass_run_dirs: Sequence[Path],
    fail_run_dirs: Sequence[Path],
    *,
    scenario: str,
    scenario_hash: str | None = None,
) -> CrossRunTriageContext | None:
    """Build the cross-run triage context for one flaky `scenario` from its runs.

    Gathers `scenario`'s evidence from each passing and failing run (skipping runs that lack a
    readable manifest or the named scenario) and reads the scenario definition and the failing
    step's selector id from the first failing run that yields them.

    Returns None unless both a passing and a failing run provide evidence for `scenario`: the
    contrast between a pass and a fail is the whole diagnosis, so with one side missing there is
    no intermittency to reason about.
    """
    passing = [ev for d in pass_run_dirs if (ev := _run_evidence(d, scenario)) is not None]
    failing = [ev for d in fail_run_dirs if (ev := _run_evidence(d, scenario)) is not None]
    if not failing or not passing:
        return None
    scenario_yaml, target_id = "", None
    for run_dir in fail_run_dirs:
        parsed = _load_scenario(run_dir, scenario)
        if parsed is None:
            continue
        scenario_yaml = dump_scenarios([parsed])
        evidence = _run_evidence(run_dir, scenario)
        fs = evidence.failed_step if evidence is not None else None
        if fs is not None and 0 <= fs.index < len(parsed.steps):
            target_id = _target_id(parsed.steps[fs.index])
        break
    return CrossRunTriageContext(
        scenario, scenario_hash, scenario_yaml, target_id, passing, failing
    )


# --- the default rule-based agent ---


def _ids(elements: list[base.Element]) -> list[str]:
    return [ident for e in elements if (ident := e.get("identifier"))]


def _close(target: str, elements: list[base.Element]) -> list[str]:
    return difflib.get_close_matches(target, _ids(elements), n=3, cutoff=0.6)


class HeuristicTriageAgent:
    """A deterministic, rule-based triage (no AI).

    Categorizes the failure by its shape and points at the likely fix — including a "did you mean"
    when the target id is absent but a similar id is on screen (the classic self-heal: an id was
    renamed).
    """

    def triage(self, context: TriageContext) -> Triage:
        fs = context.failed_step
        absent = bool(
            context.target_id
            and context.elements
            and context.target_id not in _ids(context.elements)
        )
        hints = []
        fix: Fix | None = None
        if absent and context.target_id:
            close = _close(context.target_id, context.elements)
            hints.append(
                f"`{context.target_id}` is not on the captured screen"
                + (
                    f" — did you mean {', '.join('`' + c + '`' for c in close)}?"
                    if close
                    else " (its id may have changed, or the screen differs from expected)."
                )
            )
            if close:  # a confident rename — the deterministic, whole-token self-heal
                fix = Fix(
                    "renameId",
                    fix_summary("renameId", context.target_id, close[0]),
                    context.target_id,
                    close[0],
                )

        if fs is not None and fs.action == "wait":
            sugg = [
                *hints,
                "Raise the wait timeout, or check the awaited element/condition is reachable.",
            ]
            return Triage(
                "A wait condition was not met before its timeout.", "timing", sugg, fix=fix
            )

        if fs is not None and fs.action in _ACT_TARGETS:
            if "件一致" in fs.reason and context.target_id:
                sugg = [
                    f"`{context.target_id}` matched multiple elements — add `within` or `index` to disambiguate."
                ]
            else:
                sugg = hints or [
                    "Verify the selector resolves to exactly one element (see the element tree)."
                ]
            return Triage(
                f"The `{fs.action}` step could not resolve or act on its target.",
                "selector",
                sugg,
                fix=fix,
            )

        if context.failed_expectations:
            sugg = [
                *hints,
                "Compare each failed expectation below with the screen state at the end of the run.",
            ]
            return Triage("An expectation did not hold.", "assertion", sugg)

        return Triage(
            context.failure or "The scenario failed.",
            "unknown",
            ["Inspect the run with `bajutsu trace` and the captured screenshots / logs."],
        )


# --- rendering ---


def render(context: TriageContext, triage: Triage) -> str:
    """Render a triage as a text report: the failure, diagnosis, and suggested fixes."""
    lines = [f"triage · {context.scenario}", f"  failure: {context.failure}"]
    if context.failed_step is not None:
        fs = context.failed_step
        lines.append(f"  failed step: [{fs.index}] {fs.action} — {fs.reason}")
    lines.extend(f"  failed expect: {exp}" for exp in context.failed_expectations)
    if context.evidence:
        lines.append(f"  evidence: {' · '.join(context.evidence)}")
    lines += ["", f"diagnosis [{triage.category}]: {triage.summary}", "suggested fixes:"]
    lines += [f"  - {s}" for s in triage.suggestions]
    if triage.fix is not None:
        lines.append(
            f"applicable fix: {triage.fix.summary} (apply with `triage --apply <scenario-file>`)"
        )
    return "\n".join(lines)

"""Test impact analysis — the scenario steps a source change is likely to affect (BE-0321).

The reverse of the coverage map (BE-0050): where `coverage` walks the suite forward to the app
surface it exercises, this inverts the same static scenario analysis into a map from each stable id,
screen, and asserted endpoint to the `(scenario, step)` pairs that reference it. A change — read as a
`git` diff — is turned into a *touched set* by plain string match (each referenced literal tested
against the diff's added/removed lines), and joined back through the index to the affected steps.

Deterministic and app-agnostic: the same diff and scenarios always yield the same affected set, the
match needs no per-language parsing, and no model is consulted. Soundness is bounded in both
directions and the bounds are surfaced, not hidden — a change that edits no referenced literal is
*unattributable* (it maps to no reference), so the report flags itself incomplete and a full run is
warranted; a short or common literal can widen the set past the truly-affected steps (over-selection,
the safe direction for CI). Read-only and advisory, of a piece with `audit` / `coverage` / `stats`:
it never runs a scenario, never touches a device, and never gates CI (BE-0257).
"""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.analysis.audit import scenario_matchable_ids, step_matchable_ids
from bajutsu.analysis.coverage import referenced_requests, step_requests
from bajutsu.scenario import STEP_ACTIONS, RequestMatch, Scenario, Step

# The YAML key each action field serializes to, so a step's label reads as its author wrote it
# (`assert_` → `assert`). Derived from the model, like `STEP_ACTIONS`, so a new action needs no edit.
_ACTION_KEYS = {f: Step.model_fields[f].alias or f for f in STEP_ACTIONS}


@dataclass(frozen=True, order=True)
class StepRef:
    """One scenario step (or scenario-level position) a reference points at.

    `index` is the step's 1-origin position in the scenario's step list; `0` marks a scenario-level
    reference that no single step owns — a `preconditions` screen or a scenario-level `expect`.
    """

    scenario: str
    index: int
    label: str  # `step.name`, else the action key (`tap`), else `setup` / `deeplink` / `expect`


@dataclass(frozen=True, order=True)
class Reference:
    """A literal a change can touch: a stable id, a screen name/deeplink, or an asserted endpoint."""

    kind: str  # "id" | "screen" | "endpoint"
    value: str


@dataclass(frozen=True)
class ReverseIndex:
    """Each referenced literal mapped to the steps that reference it (both sides sorted). Pure."""

    entries: dict[Reference, list[StepRef]]


@dataclass(frozen=True)
class ChangedFile:
    """One file a diff changed: its path and the bodies of its added/removed lines (prefix stripped).

    `binary` marks a change whose content cannot be string-matched at all — a binary hunk, or an
    untracked file that could not be read as text. Such a change carries no `lines`, yet unlike a pure
    rename (which also has no `lines`) it *is* a real content change, so it is always unattributable:
    the scan can never vouch for it, and `complete` must fall to False rather than silently pass it.
    """

    path: str
    lines: list[str]
    binary: bool = False


@dataclass(frozen=True, order=True)
class TouchedRef:
    """A referenced literal the diff touched, with the changed files whose lines carry it."""

    reference: Reference
    files: list[str]


@dataclass(frozen=True, order=True)
class AffectedStep:
    """A step the change is likely to affect, with the references that implicate it (the *why*)."""

    step: StepRef
    reasons: list[Reference]


@dataclass(frozen=True)
class Impact:
    """The affected steps a change selects, plus the soundness signal a CI narrowing must respect."""

    affected: list[AffectedStep]  # steps a touched reference points at, sorted
    touched: list[TouchedRef]  # the referenced literals the diff touched, sorted
    unattributable: list[str]  # changed files that touched no referenced literal (sorted, de-duped)
    complete: (
        bool  # no unattributable change — else a full run is warranted (conservative fallback)
    )


def _step_label(step: Step, index: int) -> str:
    """A human label for a step: its `name`, else its action key, else its position."""
    if step.name:
        return step.name
    action = next((_ACTION_KEYS[f] for f in STEP_ACTIONS if getattr(step, f) is not None), None)
    return action or f"step {index}"


def _request_literals(requests: list[RequestMatch]) -> set[str]:
    """The endpoint literals a change could touch — exact `url` / `path` only.

    Regex forms (`urlMatches` / `pathMatches`) are patterns, not literals that appear verbatim in app
    source, so they are not string-matched against a diff.
    """
    return {v for r in requests for v in (r.url, r.path) if v}


def reverse_index(scenarios: list[Scenario]) -> ReverseIndex:
    """Invert the suite's static references into `literal → [step]`. Pure.

    For each scenario: stable ids and asserted endpoints are attributed per step; a `preconditions`
    screen (`setup` / `deeplink`) and any scenario-level `expect` reference — which no single step
    owns — are attributed to a scenario-level `StepRef` (index 0). Reuses BE-0050's per-step selector
    and endpoint walks, so a new reference source is added in one place, not two.
    """
    by_ref: dict[Reference, set[StepRef]] = {}

    def add(kind: str, value: str, ref: StepRef) -> None:
        by_ref.setdefault(Reference(kind, value), set()).add(ref)

    for s in scenarios:
        step_ids: set[str] = set()
        step_endpoints: set[str] = set()
        for i, step in enumerate(s.steps, start=1):
            ref = StepRef(s.name, i, _step_label(step, i))
            for rid in step_matchable_ids(step):
                add("id", rid, ref)
                step_ids.add(rid)
            for ep in _request_literals(list(step_requests(step))):
                add("endpoint", ep, ref)
                step_endpoints.add(ep)

        # Scenario-level screens: a `setup` routine and a `deeplink` reach a screen before any step.
        for kind, value in (
            ("screen", s.preconditions.setup),
            ("screen", s.preconditions.deeplink),
        ):
            if value:
                add(kind, value, StepRef(s.name, 0, kind))
        # Scenario-level `expect`: ids / endpoints referenced by no step belong to the scenario itself.
        expect_ref = StepRef(s.name, 0, "expect")
        for rid in scenario_matchable_ids(s) - step_ids:
            add("id", rid, expect_ref)
        for ep in _request_literals(referenced_requests(s)) - step_endpoints:
            add("endpoint", ep, expect_ref)

    entries = {ref: sorted(steps) for ref, steps in by_ref.items()}
    return ReverseIndex(entries=entries)


def impact(index: ReverseIndex, changed: list[ChangedFile]) -> Impact:
    """Select the steps a change affects by matching referenced literals against the diff. Pure.

    A reference is *touched* when its literal appears (plain substring) on any changed line — the
    match that keeps this app-agnostic (BE-0321). Each affected step carries the touched references
    that implicate it. A change the scan cannot vouch for is *unattributable*, and forces `complete`
    to False so a CI narrowing falls back to the full suite (the conservative fallback — over-selection
    is the safe direction, silent skipping is not). Two kinds are unattributable: a text change whose
    lines match no referenced literal, and a `binary` change (a binary hunk or an unreadable file) the
    string scan can never see into. A pure rename — no lines and not binary — altered no content and is
    neither attributed nor unattributable.
    """
    touched: list[TouchedRef] = []
    reasons_by_step: dict[StepRef, set[Reference]] = {}
    attributed_files: set[str] = set()
    for ref, steps in index.entries.items():
        files = [f.path for f in changed if any(ref.value in line for line in f.lines)]
        if not files:
            continue
        touched.append(TouchedRef(reference=ref, files=sorted(dict.fromkeys(files))))
        attributed_files.update(files)
        for step in steps:
            reasons_by_step.setdefault(step, set()).add(ref)

    affected = [
        AffectedStep(step=step, reasons=sorted(reasons))
        for step, reasons in reasons_by_step.items()
    ]
    unattributable = sorted(
        {f.path for f in changed if f.path not in attributed_files and (f.binary or f.lines)}
    )
    return Impact(
        affected=sorted(affected),
        touched=sorted(touched),
        unattributable=unattributable,
        complete=not unattributable,
    )


def parse_diff(text: str) -> list[ChangedFile]:
    """Parse a unified (`git`) diff into per-file added/removed line bodies. Pure.

    Reads each file's path from its `+++ b/<path>` header (falling back to `--- a/<path>` for a
    deletion, whose new side is `/dev/null`), and collects the bodies of `+`/`-` lines **inside hunks**
    (after a `@@` header). Tracking the hunk boundary is what lets an added line whose own text starts
    with `+`/`-` (`++counter`) be collected rather than mistaken for a `+++`/`---` path header — the
    header lines precede the first `@@`, the content lines follow it. A `Binary files … differ` (or
    `GIT binary patch`) section marks the file `binary`, since a binary change cannot be string-matched
    and must count as unattributable. An unrecognized or empty diff yields no files rather than an
    error — the caller decides what an empty change means.
    """
    files: list[ChangedFile] = []
    path: str | None = None
    lines: list[str] = []
    binary = False
    in_hunk = False

    def flush() -> None:
        nonlocal path, lines, binary, in_hunk
        if path is not None:
            files.append(ChangedFile(path=path, lines=lines, binary=binary))
        path, lines, binary, in_hunk = None, [], False, False

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()  # a new file section starts; commit the previous one
            # Seed the path from the header so a binary-only section (`GIT binary patch`, which
            # carries no `+++`/`---` lines) keys by its real path; a later `+++ b/…` overrides it.
            _, _, after_b = line.partition(" b/")
            if after_b:
                path = after_b
        elif line.startswith("@@"):
            in_hunk = True  # subsequent `+`/`-` lines are content, not path headers
        elif line.startswith(("Binary files ", "GIT binary patch")):
            binary = True
            path = path or _binary_path(line)
        elif not in_hunk and line.startswith("+++ "):
            new = _diff_header_path(line[4:])
            if new is not None:
                path = new
        elif not in_hunk and line.startswith("--- "):
            old = _diff_header_path(line[4:])
            if path is None and old is not None:  # deletion: new side is /dev/null, key by old path
                path = old
        elif in_hunk and line[:1] in ("+", "-"):
            lines.append(line[1:])  # an added / removed line body inside the hunk
    flush()
    return files


def _diff_header_path(raw: str) -> str | None:
    """The path from a `--- a/x` / `+++ b/x` header, stripping the `a/` `b/` prefix; None for /dev/null."""
    field = raw.split("\t", 1)[
        0
    ].strip()  # a rename/space path may carry a trailing tab-separated ts
    if field == "/dev/null":
        return None
    if field.startswith(("a/", "b/")):
        return field[2:]
    return field


def _binary_path(line: str) -> str | None:
    """The changed path from a `Binary files a/x and b/x differ` line (its new side, else the old)."""
    inner = line.removeprefix("Binary files ").removesuffix(" differ")
    a, _, b = inner.partition(" and ")
    return _diff_header_path(b) or _diff_header_path(a) or None


def render(report: Impact) -> str:
    """Human-readable summary: the affected steps and why, then the soundness signal."""
    n = len(report.affected)
    lines = [f"affected steps: {n}" + (" (none)" if n == 0 else "")]
    for a in report.affected:
        where = f"{a.step.scenario} > {a.step.label}" + (
            f" (step {a.step.index})" if a.step.index else ""
        )
        why = ", ".join(f"{r.kind}:{r.value}" for r in a.reasons)
        lines.append(f"  {where} — {why}")
    if report.complete:
        lines.append("complete: every analyzed change maps to a referenced literal")
    else:
        lines.append(
            "incomplete: unattributable change(s) — run the full suite "
            f"({len(report.unattributable)} file(s)): {report.unattributable}"
        )
    return "\n".join(lines)

"""Convention score for an app — how ready it is to be tested.

Pure scoring computed from a screen (a list of Element): id coverage over
actionable elements, namespace conformance, and id uniqueness. AI is not
involved. Environment/connection gates (which need a device) live in the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.drivers import base

# Traits that count as "actionable" (the denominator for id coverage).
ACTIONABLE_TRAITS = {
    "button",
    "link",
    "textField",
    "searchField",
    "textView",
    "switch",
    "slider",
    "tab",
    "cell",
}

OK_COVERAGE = 0.9
FAIL_COVERAGE = 0.7


@dataclass(frozen=True)
class Score:
    actionable: int
    with_id: int
    id_coverage: float
    namespace_conformance: float
    duplicate_ids: int
    grade: str  # "Ready" | "Partial" | "Blocked"
    missing_id: list[base.Element]  # actionable elements without an id
    off_namespace: list[str]        # ids whose first segment is not a declared namespace
    duplicates: list[str]           # ids that appear 2+ times on the screen


def _is_actionable(el: base.Element) -> bool:
    return bool(set(el["traits"]) & ACTIONABLE_TRAITS)


def _namespace(identifier: str) -> str:
    return identifier.split(".", 1)[0]


def score(elements: list[base.Element], id_namespaces: list[str]) -> Score:
    """Compute the convention score for one screen."""
    actionable = [e for e in elements if _is_actionable(e)]
    with_id = sum(1 for e in actionable if e["identifier"])
    id_coverage = with_id / len(actionable) if actionable else 1.0

    ids: list[str] = []
    for e in elements:
        ident = e["identifier"]
        if ident:
            ids.append(ident)

    namespaces = set(id_namespaces)
    if namespaces and ids:
        conforming = sum(1 for i in ids if _namespace(i) in namespaces)
        namespace_conformance = conforming / len(ids)
        off_namespace = [i for i in ids if _namespace(i) not in namespaces]
    else:
        namespace_conformance = 1.0
        off_namespace = []

    seen: set[str] = set()
    duplicates: list[str] = []
    for i in ids:
        if i in seen and i not in duplicates:
            duplicates.append(i)
        seen.add(i)

    if duplicates or id_coverage < FAIL_COVERAGE:
        grade = "Blocked"
    elif id_coverage >= OK_COVERAGE and namespace_conformance >= 1.0:
        grade = "Ready"
    else:
        grade = "Partial"

    return Score(
        actionable=len(actionable),
        with_id=with_id,
        id_coverage=id_coverage,
        namespace_conformance=namespace_conformance,
        duplicate_ids=len(duplicates),
        grade=grade,
        missing_id=[e for e in actionable if not e["identifier"]],
        off_namespace=off_namespace,
        duplicates=duplicates,
    )


def render(s: Score) -> str:
    """Human-readable summary that points at what to fix."""
    lines = [
        f"grade: {s.grade}",
        f"idCoverage: {s.id_coverage:.2f} ({s.with_id}/{s.actionable})",
        f"namespaceConformance: {s.namespace_conformance:.2f}",
        f"duplicateIds: {s.duplicate_ids}",
    ]
    for e in s.missing_id:
        lines.append(f"  missing id: label={e['label']!r} traits={e['traits']} frame={e['frame']}")
    if s.off_namespace:
        lines.append(f"  off-namespace ids: {s.off_namespace}")
    if s.duplicates:
        lines.append(f"  duplicate ids: {s.duplicates}")
    return "\n".join(lines)

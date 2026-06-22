"""Static e2e coverage map for a scenario suite — which declared id namespaces it touches.

The read-only counterpart to doctor's per-screen convention score: doctor grades the ids an app
*exposes* on one screen, this grades the ids a *suite* exercises. It walks every scenario without a
device (reusing audit's selector walk), groups the stable ids it references by namespace, and
measures them against the app's declared `idNamespaces` — reporting per-namespace coverage, the
gap list (declared namespaces no scenario touches), and off-namespace ids (referenced ids whose
namespace was never declared). No model is consulted, no scenario is run, and no verdict is
touched: a coverage report is advisory, never a CI gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.audit import referenced_ids
from bajutsu.doctor import namespace_of
from bajutsu.scenario import Scenario


@dataclass(frozen=True)
class NamespaceCoverage:
    """One declared namespace the suite touches, with the referenced ids that touch it."""

    namespace: str
    ids: list[str]  # the distinct referenced id-strings under this namespace (sorted)


@dataclass(frozen=True)
class Coverage:
    """How a scenario suite's stable-id references cover an app's declared namespaces."""

    namespaces: list[
        NamespaceCoverage
    ]  # declared namespaces the suite references, in declared order
    gaps: list[str]  # declared namespaces no scenario references
    off_namespace: list[str]  # referenced ids whose namespace was never declared
    total: int  # declared namespaces
    covered: int  # declared namespaces with at least one referenced id
    coverage: float  # covered / total (1.0 when no namespaces are declared)


def coverage(scenarios: list[Scenario], id_namespaces: list[str]) -> Coverage:
    """Measure a suite's stable-id references against the app's declared namespaces. Pure."""
    declared = list(dict.fromkeys(id_namespaces))  # de-dupe, keep declared order
    declared_set = set(declared)
    referenced = sorted({rid for s in scenarios for rid in referenced_ids(s)})

    by_namespace: dict[str, list[str]] = {}
    off_namespace: list[str] = []
    for rid in referenced:
        ns = namespace_of(rid)
        if ns in declared_set:
            by_namespace.setdefault(ns, []).append(rid)
        else:
            off_namespace.append(rid)

    namespaces = [NamespaceCoverage(ns, by_namespace[ns]) for ns in declared if ns in by_namespace]
    gaps = [ns for ns in declared if ns not in by_namespace]
    return Coverage(
        namespaces=namespaces,
        gaps=gaps,
        off_namespace=off_namespace,
        total=len(declared),
        covered=len(namespaces),
        coverage=len(namespaces) / len(declared) if declared else 1.0,
    )


def render(c: Coverage) -> str:
    """Human-readable summary that points at the untested namespaces."""
    lines = [f"coverage: {c.coverage:.2f} ({c.covered}/{c.total})"]
    lines.extend(f"  {ns.namespace}: {ns.ids}" for ns in c.namespaces)
    if c.gaps:
        lines.append(f"  gaps (no scenario references): {c.gaps}")
    if c.off_namespace:
        lines.append(f"  off-namespace ids: {c.off_namespace}")
    return "\n".join(lines)

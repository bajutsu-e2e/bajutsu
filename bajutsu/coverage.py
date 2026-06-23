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

from collections.abc import Iterator
from dataclasses import dataclass

from bajutsu.assertions import match_request, request_label
from bajutsu.audit import referenced_ids
from bajutsu.doctor import namespace_of
from bajutsu.network import NetworkExchange
from bajutsu.scenario import Assertion, RequestMatch, Scenario, Step


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


# --- endpoint coverage: observed traffic (network.json) vs the endpoints the suite asserts on ---


@dataclass(frozen=True)
class EndpointCoverage:
    """How a suite's network assertions cover the endpoints its runs actually hit."""

    observed: list[str]  # distinct "METHOD path" seen across the run set (sorted)
    asserted: list[str]  # observed endpoints some declared matcher matches
    unasserted: list[str]  # observed endpoints no matcher matches — untested traffic
    declared_unobserved: list[str]  # matcher labels that matched no observed exchange
    coverage: float  # asserted / observed (1.0 when nothing was observed)


def _step_requests(step: Step) -> Iterator[RequestMatch]:
    """Every network-endpoint matcher a step declares, recursing into control flow."""
    for a in step.assert_ or []:
        yield from _assertion_requests(a)
    if step.if_ is not None:
        yield from _assertion_requests(step.if_.condition)
        for nested in (*step.if_.then, *(step.if_.else_ or [])):
            yield from _step_requests(nested)
    if step.for_each is not None:
        for nested in step.for_each.steps:
            yield from _step_requests(nested)


def _assertion_requests(a: Assertion) -> Iterator[RequestMatch]:
    """The endpoint matcher(s) a network assertion declares (request / event / requestSequence).

    `event` is reduced to its endpoint fields (its body/count are not about *which* endpoint), so
    every form yields a `RequestMatch` the observed exchanges can be tested against."""
    if a.request is not None:
        yield a.request
    if a.event is not None:
        e = a.event
        endpoint = (e.method, e.url, e.url_matches, e.path, e.path_matches)
        # A body-only event pins no endpoint, so it contributes no endpoint matcher (and a
        # RequestMatch with every field None would fail its own "at least one criterion" validator).
        if any(v is not None for v in endpoint):
            yield RequestMatch(
                method=e.method,
                url=e.url,
                urlMatches=e.url_matches,
                path=e.path,
                pathMatches=e.path_matches,
            )
    if a.request_sequence is not None:
        yield from a.request_sequence


def referenced_requests(scenario: Scenario) -> list[RequestMatch]:
    """Every network-endpoint matcher a scenario declares — across steps' `assert`s, nested control
    flow, and scenario-level `expect`. The endpoint side of the coverage map (BE-0048 assertions)."""
    return [
        *(r for step in scenario.steps for r in _step_requests(step)),
        *(r for a in scenario.expect for r in _assertion_requests(a)),
    ]


def _endpoint(ex: NetworkExchange) -> str:
    return f"{ex.method.upper()} {ex.path}"


def endpoint_coverage(
    scenarios: list[Scenario], exchanges: list[NetworkExchange]
) -> EndpointCoverage:
    """Measure how the suite's network assertions cover the endpoints its runs hit. Pure: the
    observed exchanges come from `network.json`, the declared matchers from the scenarios."""
    matchers = [m for s in scenarios for m in referenced_requests(s)]
    observed = sorted({_endpoint(ex) for ex in exchanges})
    asserted_eps = {
        _endpoint(ex) for ex in exchanges if any(match_request(ex, m) for m in matchers)
    }
    declared_unobserved = sorted(
        {request_label(m) for m in matchers if not any(match_request(ex, m) for ex in exchanges)}
    )
    asserted = sorted(asserted_eps)
    return EndpointCoverage(
        observed=observed,
        asserted=asserted,
        unasserted=[e for e in observed if e not in asserted_eps],
        declared_unobserved=declared_unobserved,
        coverage=len(asserted) / len(observed) if observed else 1.0,
    )


def render_endpoints(ec: EndpointCoverage) -> str:
    """Human-readable endpoint-coverage summary that points at untested traffic."""
    lines = [
        f"endpoints: {ec.coverage:.2f} ({len(ec.asserted)}/{len(ec.observed)} observed asserted)"
    ]
    if ec.unasserted:
        lines.append(f"  unasserted (observed, no assertion): {ec.unasserted}")
    if ec.declared_unobserved:
        lines.append(f"  declared but not observed: {ec.declared_unobserved}")
    return "\n".join(lines)

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

import functools
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from bajutsu.assertions import match_request, request_label
from bajutsu.audit import referenced_ids
from bajutsu.doctor import namespace_of
from bajutsu.network import NetworkExchange
from bajutsu.scenario import Assertion, RequestMatch, Scenario, Step, WaitRequest


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
    if step.wait is not None and isinstance(step.wait.until, WaitRequest):
        yield step.wait.until.request  # `wait: { until: { request: ... } }` declares an endpoint too
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
    every form yields a `RequestMatch` the observed exchanges can be tested against.
    """
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
    """Every network-endpoint matcher a scenario declares.

    Covers steps' `assert`s, nested control flow, and scenario-level `expect` — the endpoint side of
    the coverage map (BE-0048 assertions).
    """
    return [
        *(r for step in scenario.steps for r in _step_requests(step)),
        *(r for a in scenario.expect for r in _assertion_requests(a)),
    ]


def _endpoint(ex: NetworkExchange) -> str:
    return f"{ex.method.upper()} {ex.path}"


def endpoint_coverage(
    scenarios: list[Scenario], exchanges: list[NetworkExchange]
) -> EndpointCoverage:
    """Measure how the suite's network assertions cover the endpoints its runs hit.

    Pure: the observed exchanges come from `network.json`, the declared matchers from the scenarios.
    """
    matchers = [m for s in scenarios for m in referenced_requests(s)]
    observed = sorted({_endpoint(ex) for ex in exchanges})
    # One pass over the matcher-vs-exchange relation: an exchange's endpoint is *asserted* if any
    # matcher matches it, and a matcher is *unobserved* if no exchange matches it. Deriving both in
    # a single sweep avoids re-computing the same relation twice (it was O(N*E), computed twice).
    asserted_eps: set[str] = set()
    matched_matchers: set[int] = set()
    for ex in exchanges:
        hit = False
        for i, m in enumerate(matchers):
            # Once this exchange is already asserted, a matcher we've *already* seen match some
            # exchange can be skipped — it changes neither result. A not-yet-matched matcher is
            # always tested (it may be the one this exchange asserts, and we must learn whether it
            # ever matches, for `declared_unobserved`), so the relation stays exact while broad /
            # overlapping matchers stop re-matching every exchange.
            if hit and i in matched_matchers:
                continue
            if match_request(ex, m):
                hit = True
                matched_matchers.add(i)
        if hit:
            asserted_eps.add(_endpoint(ex))
    declared_unobserved = sorted(
        {request_label(m) for i, m in enumerate(matchers) if i not in matched_matchers}
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


# --- observed-id coverage: ids rendered across a run set (elements.json) vs declared namespaces ---


@dataclass(frozen=True)
class ObservedIdCoverage:
    """Which declared namespaces a run set actually rendered ids under.

    The run-evidence counterpart to `Coverage` (static references): `coverage()` grades the ids the
    scenarios *write* (statically reference); this grades the ids the runs *showed* (observed across
    every `elements.json`), exposing namespaces the suite never exercised at runtime.
    """

    namespaces: list[
        NamespaceCoverage
    ]  # declared namespaces with at least one observed id, in declared order
    unobserved: list[str]  # declared namespaces rendered in no run
    off_namespace: list[str]  # observed ids whose namespace was never declared
    total: int  # declared namespaces
    covered: int  # declared namespaces with at least one observed id
    coverage: float  # covered / total (1.0 when no namespaces are declared)


def observed_id_coverage(observed_ids: list[str], id_namespaces: list[str]) -> ObservedIdCoverage:
    """Group observed ids by the app's declared namespaces.

    Pure: the observed ids come from the run set's `elements.json` files, the namespaces from the app
    config. Mirrors `coverage()`.
    """
    declared = list(dict.fromkeys(id_namespaces))  # de-dupe, keep declared order
    declared_set = set(declared)
    observed = sorted(set(observed_ids))

    by_namespace: dict[str, list[str]] = {}
    off_namespace: list[str] = []
    for oid in observed:
        ns = namespace_of(oid)
        if ns in declared_set:
            by_namespace.setdefault(ns, []).append(oid)
        else:
            off_namespace.append(oid)

    namespaces = [NamespaceCoverage(ns, by_namespace[ns]) for ns in declared if ns in by_namespace]
    return ObservedIdCoverage(
        namespaces=namespaces,
        unobserved=[ns for ns in declared if ns not in by_namespace],
        off_namespace=off_namespace,
        total=len(declared),
        covered=len(namespaces),
        coverage=len(namespaces) / len(declared) if declared else 1.0,
    )


def render_observed_ids(oc: ObservedIdCoverage) -> str:
    """Human-readable summary that points at namespaces the runs never rendered."""
    lines = [f"observed ids: {oc.coverage:.2f} ({oc.covered}/{oc.total})"]
    lines.extend(f"  {ns.namespace}: {ns.ids}" for ns in oc.namespaces)
    if oc.unobserved:
        lines.append(f"  unobserved (no run rendered): {oc.unobserved}")
    if oc.off_namespace:
        lines.append(f"  off-namespace ids: {oc.off_namespace}")
    return "\n".join(lines)


# --- screens-visited: screens a crawl discovered vs the screens a run set actually reached ---


@dataclass(frozen=True)
class ScreenRef:
    """A discovered screen: its crawl fingerprint and a human label (its first id, or short hash)."""

    fingerprint: str
    label: str


@dataclass(frozen=True)
class ScreenCoverage:
    """How much of a crawl's discovered screen surface a run set actually reached."""

    visited: list[ScreenRef]  # discovered screens a run rendered, in fingerprint order
    unvisited: list[ScreenRef]  # discovered screens no run reached — the gap
    total: int  # discovered screens
    covered: int  # discovered screens visited
    coverage: float  # covered / total (1.0 when nothing was discovered)


def screen_coverage(discovered: list[ScreenRef], visited: frozenset[str]) -> ScreenCoverage:
    """Measure how many crawl-discovered screens a run set reached. Pure.

    `discovered` is the crawl's screen-map nodes (de-duped here by fingerprint); `visited` is the
    set of screen fingerprints the runs rendered, computed by the same `crawl.fingerprint` so the
    two are comparable. A run fingerprint the crawl never found cannot inflate coverage — only the
    discovered set is the denominator.
    """
    by_fp = {s.fingerprint: s for s in reversed(discovered)}  # de-dupe, keep first listed
    refs = sorted(by_fp.values(), key=lambda s: s.fingerprint)
    seen = [s for s in refs if s.fingerprint in visited]
    return ScreenCoverage(
        visited=seen,
        unvisited=[s for s in refs if s.fingerprint not in visited],
        total=len(refs),
        covered=len(seen),
        coverage=len(seen) / len(refs) if refs else 1.0,
    )


def render_screens(sc: ScreenCoverage) -> str:
    """Human-readable summary that points at the discovered screens no run reached."""
    lines = [f"screens visited: {sc.coverage:.2f} ({sc.covered}/{sc.total})"]
    if sc.unvisited:
        lines.append(f"  unvisited (discovered, no run reached): {[s.label for s in sc.unvisited]}")
    return "\n".join(lines)


# --- filesystem evidence readers: the run set's captured artifacts (shared CLI + serve, BE-0146) ---


def _evidence_files(runs_dir: Path, name: str, run_ids: Iterable[str] | None) -> list[Path]:
    """Every per-step ``<run>/<step>/<name>`` under *runs_dir*, optionally restricted to *run_ids*.

    ``run_ids`` None reads the whole runs dir (the CLI's ``--runs <dir>``); a set restricts to those
    runs (the serve view's selected run set). Callers pass only validated single-segment run ids, so a
    restricted read never globs outside a run's own tree.
    """
    if run_ids is None:
        return sorted(runs_dir.glob(f"*/*/{name}"))
    return sorted(f for rid in run_ids for f in (runs_dir / rid).glob(f"*/{name}"))


def read_exchanges(runs_dir: Path, run_ids: Iterable[str] | None = None) -> list[NetworkExchange]:
    """Every network exchange recorded across the run set.

    The union of each ``network.json`` under *runs_dir* (read-only; a malformed/partial file is
    skipped wholesale, not fatal — a bad entry never leaves a half-read batch).
    """
    exchanges: list[NetworkExchange] = []
    for net in _evidence_files(runs_dir, "network.json", run_ids):
        try:
            data = json.loads(net.read_text(encoding="utf-8"))
            if not isinstance(data, list):  # a scalar/object file isn't an exchange list — skip it
                continue
            batch = [NetworkExchange.model_validate(e) for e in data if isinstance(e, dict)]
        except (OSError, ValueError):
            continue
        exchanges.extend(batch)
    return exchanges


def read_observed_ids(runs_dir: Path, run_ids: Iterable[str] | None = None) -> list[str]:
    """Every stable id rendered across the run set.

    The union of each element's ``identifier`` from every per-step ``elements.json`` under *runs_dir*
    (read-only; a malformed/partial file is skipped, not fatal). Null and empty identifiers are
    dropped — only elements that carry a stable id contribute.
    """
    ids: list[str] = []
    for els in _evidence_files(runs_dir, "elements.json", run_ids):
        try:
            data = json.loads(els.read_text(encoding="utf-8"))
            if not isinstance(data, list):  # a scalar/object file isn't an element list — skip it
                continue
        except (OSError, ValueError):  # unreadable or invalid JSON — skip, like read_exchanges
            continue
        ids.extend(
            e["identifier"]
            for e in data
            if isinstance(e, dict) and isinstance(e.get("identifier"), str) and e["identifier"]
        )
    return ids


# --- HTML report: the dimensions visualized on one self-contained page (BE-0050) ---

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    # autoescape so a stray "<" in an id can never inject markup into the page.
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


def render_html(
    c: Coverage,
    endpoints: EndpointCoverage | None = None,
    observed: ObservedIdCoverage | None = None,
    screens: ScreenCoverage | None = None,
    target: str = "",
) -> str:
    """A self-contained HTML coverage report (inline CSS, no JS, no external asset).

    The visual counterpart to the `render*` text summaries: the static id-namespace dimension
    always renders; the endpoint, observed-id, and screens-visited dimensions render only when run
    evidence (and, for screens, a crawl map) supplies them. Read-only and AI-free, like every other
    coverage output.
    """
    return (
        _env()
        .get_template("coverage.html.j2")
        .render(static=c, endpoints=endpoints, observed=observed, screens=screens, target=target)
    )

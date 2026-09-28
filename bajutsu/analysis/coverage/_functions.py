"""Derive each coverage view from scenarios and run evidence, and render it as text or HTML."""

from __future__ import annotations

import functools
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader

from bajutsu.analysis.audit import referenced_ids
from bajutsu.common.assertions import match_request, request_label
from bajutsu.common.doctor import namespace_of
from bajutsu.common.drivers import base
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.scenario import Assertion, RequestMatch, Scenario, Step, WaitRequest
from bajutsu.common.scenario.interp import find_tokens
from bajutsu.crawl import fingerprint

from .coverage import Coverage
from .endpoint_coverage import EndpointCoverage
from .namespace_coverage import NamespaceCoverage
from .observed_id_coverage import ObservedIdCoverage
from .screen_coverage import ScreenCoverage
from .screen_ref import ScreenRef

# --- HTML report: the dimensions visualized on one self-contained page (BE-0050) ---

# The shared Jinja templates live at the package root (`bajutsu/templates/`), one level up now
# that this module is packaged under `analysis/` (BE-0257).
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def _is_literal_id(rid: str) -> bool:
    """Whether *rid* is a real id rather than a placeholder bound at run time.

    The loader has already expanded components (`params.*`) and data rows (`row.*`), so a `${...}`
    left in a selector is a runtime binding — `vars.*` (a `totp` / `email` step's output, BE-0046) or
    `secrets.*`. Its text names no element, so counting it would put a token like `${vars.otp}` in
    the map as if it were an id and, since `namespace_of` reads up to the first `.`, invent an
    `${vars` namespace among the off-namespace gaps.
    """
    return not find_tokens(rid)


def coverage(scenarios: list[Scenario], id_namespaces: list[str]) -> Coverage:
    """Measure a suite's stable-id references against the app's declared namespaces. Pure."""
    declared = list(dict.fromkeys(id_namespaces))  # de-dupe, keep declared order
    declared_set = set(declared)
    referenced = sorted(
        {rid for s in scenarios for rid in referenced_ids(s) if _is_literal_id(rid)}
    )

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


def step_requests(step: Step) -> Iterator[RequestMatch]:
    """Every network-endpoint matcher a step declares, recursing into control flow.

    Shared by the endpoint coverage map (BE-0050) and test impact analysis (BE-0321), so the two
    read the same endpoint references from a step. Pure.
    """
    if step.wait is not None and isinstance(step.wait.until, WaitRequest):
        yield step.wait.until.request  # `wait: { until: { request: ... } }` declares an endpoint too
    for a in step.assert_ or []:
        yield from _assertion_requests(a)
    if step.if_ is not None:
        yield from _assertion_requests(step.if_.condition)
        for nested in (*step.if_.then, *(step.if_.else_ or [])):
            yield from step_requests(nested)
    if step.for_each is not None:
        for nested in step.for_each.steps:
            yield from step_requests(nested)


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
        *(r for step in scenario.steps for r in step_requests(step)),
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


def screen_refs(screenmap: dict[str, Any]) -> list[ScreenRef]:
    """The screens a crawl discovered, from a parsed ``screenmap.json``'s nodes. Pure.

    Each node's label is its first stable id, or the short fingerprint when the screen carries none.
    A node that isn't a dict or carries no ``fingerprint`` is skipped, an unexpected top-level shape
    yields no screens, and an ``ids`` that isn't a list falls back to the fingerprint — so a
    hand-corrupted map degrades to an empty or coarser denominator rather than raising. The map can
    arrive from outside the process (an uploaded run bundle, BE-0073), where a raise would surface as
    a 500 instead of the Coverage view's readable error. Shared by the CLI's ``--crawl`` and the serve
    Coverage view so the two read one map the same way.
    """
    nodes = screenmap.get("nodes")
    refs: list[ScreenRef] = []
    for n in nodes if isinstance(nodes, list) else []:
        if not isinstance(n, dict) or not n.get("fingerprint"):
            continue
        fp = str(n["fingerprint"])
        ids = n.get("ids")
        label = str(ids[0]) if isinstance(ids, list) and ids else fp[:7]
        refs.append(ScreenRef(fingerprint=fp, label=label))
    return refs


def screen_fingerprints(rendered: Iterable[list[Any]]) -> frozenset[str]:
    """The screen fingerprints a run set rendered, one per parsed ``elements.json``. Pure.

    Each per-step element list is one rendered screen, fingerprinted with the same
    `crawl.fingerprint` the crawl uses — that shared reduction is what makes a visited screen
    comparable to a discovered one. A list holding no element dicts contributes nothing, so a
    partial capture narrows the numerator instead of inventing a screen.
    """
    return frozenset(
        fingerprint(cast(list[base.Element], elements)).value
        for data in rendered
        if (elements := [e for e in data if isinstance(e, dict)])
    )


def render_screens(sc: ScreenCoverage) -> str:
    """Human-readable summary that points at the discovered screens no run reached."""
    lines = [f"screens visited: {sc.coverage:.2f} ({sc.covered}/{sc.total})"]
    if sc.unvisited:
        lines.append(f"  unvisited (discovered, no run reached): {[s.label for s in sc.unvisited]}")
    return "\n".join(lines)


# --- filesystem evidence readers: the run set's captured artifacts (shared CLI + serve, BE-0146) ---


def _evidence_files(runs_dir: Path, name: str, run_ids: Iterable[str] | None) -> list[Path]:
    """Every ``<name>`` recorded anywhere under *runs_dir*, optionally restricted to *run_ids*.

    ``run_ids`` None reads the whole runs dir (the CLI's ``--runs <dir>``); a set restricts to those
    runs (the serve view's selected run set). Callers pass only validated single-segment run ids, so a
    restricted read never globs outside a run's own tree. A recursive glob rather than a fixed
    segment count, since a scenario's own evidence directory nests a declared target's own folder
    (and, beneath it, the step folders) only once it declares two or more targets — a fixed count
    would silently miss both a non-primary target's own ``network.json`` and every ``elements.json``,
    which always sits one folder deeper than ``network.json`` regardless of target count.
    """
    if run_ids is None:
        return sorted(runs_dir.glob(f"**/{name}"))
    return sorted(f for rid in run_ids for f in (runs_dir / rid).glob(f"**/{name}"))


def read_element_lists(runs_dir: Path, run_ids: Iterable[str] | None = None) -> list[list[Any]]:
    """Every ``elements.json`` recorded across the run set, parsed as a JSON list.

    Shared by ``read_observed_ids`` (the id-coverage dimension) and the CLI's screens-visited
    dimension (``bajutsu coverage --crawl``), so the two never disagree on how ``elements.json`` is
    found or parsed — before this function existed, each carried its own copy of the same glob and
    parse loop, and both were wrong in the same way (a fixed segment count one folder too shallow).
    Read-only; an unreadable file, or one whose top level isn't a list, is skipped.
    """
    out: list[list[Any]] = []
    for els in _evidence_files(runs_dir, "elements.json", run_ids):
        try:
            data = json.loads(els.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            out.append(data)
    return out


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

    The union of each element's ``identifier`` from every ``elements.json`` `read_element_lists`
    finds (read-only; a malformed/partial file is already skipped there). Null and empty
    identifiers are dropped — only elements that carry a stable id contribute.
    """
    ids: list[str] = []
    for data in read_element_lists(runs_dir, run_ids):
        ids.extend(
            e["identifier"]
            for e in data
            if isinstance(e, dict) and isinstance(e.get("identifier"), str) and e["identifier"]
        )
    return ids


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

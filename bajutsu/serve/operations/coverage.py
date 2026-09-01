"""Coverage-map serve operation (BE-0146).

Surfaces the deterministic `bajutsu coverage` aggregation (BE-0050) in the serve Web UI: the static
id-namespace dimension always, the endpoints-observed-vs-asserted and observed-id dimensions when a
run set is selected, and the screens-visited dimension when a crawl supplies the discovered
denominator. Read-only, deterministic, AI-free: every figure is a count over declared namespaces and
`network.json` / `elements.json` / `screenmap.json`, never a verdict and never a gate.

Two entry points share one aggregation: `coverage_view` (the view's `POST /api/coverage`, which also
carries the structured figures) and `coverage_html` (`GET /coverage`, the linkable page the other
analytics dashboards each have).
"""

from __future__ import annotations

import dataclasses
import html
import json
from collections.abc import Iterable, Iterator
from typing import Any

from bajutsu.analysis import coverage as _coverage
from bajutsu.common.config import load_config, resolve
from bajutsu.evidence.network import NetworkExchange
from bajutsu.scenario import load_scenarios_dir
from bajutsu.serve.artifacts import ArtifactStore
from bajutsu.serve.authz import _target_forbidden
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.operations.reads import run_set_manifests
from bajutsu.serve.state import ServeState, _scenarios_dir_for


def _artifact_paths(manifests: list[dict[str, Any]], kind: str) -> Iterator[str]:
    """Every run-relative artifact path of *kind* referenced by *manifests* (BE-0258).

    Each parsed ``manifest.json`` carries its own ``runId`` (`bajutsu.report.manifest.manifest_dict`)
    alongside its per-scenario ``artifacts`` (scenario-level, e.g. ``network``) and per-step
    ``steps[].artifacts`` (e.g. ``elements``) entries, whose ``name`` is relative to the *run* —
    the writers (`bajutsu.runner.pipeline`, `bajutsu.evidence`) stamp it with the scenario's ``sid``
    (and, for a step artifact, the step id) at write time. Prefixing with the manifest's own
    ``runId`` gives the same path `bajutsu.analysis.coverage._evidence_files` globs for, with no store-side
    glob/list primitive needed.
    """

    def matching(run_id: str, items: Any) -> Iterator[str]:
        for artifact in items or []:
            if isinstance(artifact, dict) and artifact.get("kind") == kind:
                name = artifact.get("name")
                if isinstance(name, str) and name:
                    yield f"{run_id}/{name}"

    for manifest in manifests:
        run_id = manifest.get("runId")
        if not isinstance(run_id, str) or not valid_run_id(run_id):
            continue
        for scenario in manifest.get("scenarios") or []:
            if not isinstance(scenario, dict):
                continue
            yield from matching(run_id, scenario.get("artifacts"))
            for step in scenario.get("steps") or []:
                if isinstance(step, dict):
                    yield from matching(run_id, step.get("artifacts"))


def _read_json_lists(
    store: ArtifactStore, manifests: list[dict[str, Any]], kind: str
) -> Iterator[list[Any]]:
    """Each artifact of *kind* in *manifests*, read through *store* and parsed as a JSON list.

    Shared by `read_exchanges_via_store`/`read_observed_ids_via_store`: an artifact that can't be
    fetched, or doesn't parse to a JSON list, is skipped — the same "skip what can't be read"
    discipline `bajutsu.analysis.coverage.read_exchanges`/`read_observed_ids` apply to a local
    `runs_dir` (BE-0258).
    """
    for rel in _artifact_paths(manifests, kind):
        try:
            raw = store.open_bytes(rel)
        except OSError:
            continue  # a race (trashed/purged mid-read) or a transient store error — skip, not fatal
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, list):
            yield data


def read_exchanges_via_store(
    store: ArtifactStore, manifests: list[dict[str, Any]]
) -> list[NetworkExchange]:
    """`bajutsu.analysis.coverage.read_exchanges`'s seam-routed counterpart: every network exchange across
    *manifests* (e.g. from `run_set_manifests`), reading each scenario's ``network.json`` through
    *store* instead of globbing a local ``runs_dir`` (BE-0258)."""
    exchanges: list[NetworkExchange] = []
    for data in _read_json_lists(store, manifests, "network"):
        try:
            # Build the batch before extending: a `NetworkExchange` mid-list that fails validation
            # must drop the *whole* file's batch, not just the entries seen before it — matching
            # `bajutsu.analysis.coverage.read_exchanges`'s "a bad entry never leaves a half-read batch".
            batch = [NetworkExchange.model_validate(e) for e in data if isinstance(e, dict)]
        except ValueError:
            continue
        exchanges.extend(batch)
    return exchanges


def observed_identifiers(rendered: Iterable[list[Any]]) -> list[str]:
    """Every non-empty stable id across already-parsed ``elements.json`` lists. Pure.

    Split from `read_observed_ids_via_store` so a caller holding the parsed lists can derive the
    observed-id dimension without re-fetching them.
    """
    return [
        e["identifier"]
        for data in rendered
        for e in data
        if isinstance(e, dict) and isinstance(e.get("identifier"), str) and e["identifier"]
    ]


def read_observed_ids_via_store(store: ArtifactStore, manifests: list[dict[str, Any]]) -> list[str]:
    """`bajutsu.analysis.coverage.read_observed_ids`'s seam-routed counterpart: every stable id across
    *manifests* (e.g. from `run_set_manifests`), reading each step's ``elements.json`` through
    *store* instead of globbing a local ``runs_dir`` (BE-0258)."""
    return observed_identifiers(_read_json_lists(store, manifests, "elements"))


class _CoverageError(Exception):
    """An input the caller can fix (no config, unknown target, unreadable suite), with its status.

    Raised by `_aggregate` so the JSON and HTML entry points can report the same problem in the shape
    each one's client expects, without threading an error tuple through the aggregation.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class _Report:
    """One aggregated coverage map: the static dimension, plus whichever evidence dimensions the
    request supplied inputs for."""

    target: str
    static: _coverage.Coverage
    endpoints: _coverage.EndpointCoverage | None = None
    observed: _coverage.ObservedIdCoverage | None = None
    screens: _coverage.ScreenCoverage | None = None

    def html(self) -> str:
        return _coverage.render_html(
            self.static,
            endpoints=self.endpoints,
            observed=self.observed,
            screens=self.screens,
            target=self.target,
        )


def discovered_screens_via_store(store: ArtifactStore, crawl_run: str) -> list[_coverage.ScreenRef]:
    """The screens *crawl_run* discovered, read through *store* instead of a local `screenmap.json`.

    The seam-routed counterpart to the CLI's `--crawl` read; the node walk itself is
    `bajutsu.analysis.coverage.screen_refs`, so both surfaces label a screen the same way.

    Raises:
        _CoverageError: the crawl run's screen map is missing or doesn't parse as a JSON object. The
            run is one the caller picked from the crawl history, so dropping the dimension silently
            would read as the feature being broken.
    """
    if not valid_run_id(crawl_run):
        raise _CoverageError("invalid crawl run id")
    try:
        raw = store.open_bytes(f"{crawl_run}/screenmap.json")
    except OSError:
        raw = None
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise _CoverageError(f"crawl run '{crawl_run}' has no readable screen map")
    return _coverage.screen_refs(data)


def _aggregate(
    state: ServeState,
    target: str,
    runs: list[str],
    crawl: str,
    *,
    actor: str | None,
) -> _Report:
    """Build the coverage map for *target*, folding in each dimension its inputs support.

    Raises:
        _CoverageError: any input the caller can fix — no bound config, a missing/unknown target, a
            target with no scenarios dir, a suite that won't load, a bad run id, or a crawl selected
            without the runs that say which of its screens were visited — plus the one the caller
            cannot: a target another org owns, which raises with a 403.
    """
    if state.config is None:
        raise _CoverageError("open a config first")
    if not target:
        raise _CoverageError("target is required")

    config = load_config(state.config.read_text(encoding="utf-8"))
    if target not in config.targets:
        raise _CoverageError(f"unknown target: {target}")

    # The static dimension reads the target's suite straight off the checkout, which is not org-scoped
    # — so without this guard an actor of one org could read another org's declared namespaces, its
    # referenced ids, and its gap list (BE-0015). Single-tenant serve never forbids.
    org = state.org_of(actor)
    if _target_forbidden(state, org, target):
        raise _CoverageError("forbidden", status=403)

    scenarios_dir = _scenarios_dir_for(state, target)
    if scenarios_dir is None or not scenarios_dir.is_dir():
        raise _CoverageError(f"target '{target}' has no scenarios dir")
    try:
        scenarios = load_scenarios_dir(scenarios_dir)
    except (OSError, ValueError) as e:
        raise _CoverageError(f"failed to load scenarios: {e}") from None

    eff = resolve(config, target)
    report = _Report(target=target, static=_coverage.coverage(scenarios, eff.id_namespaces))

    # Every id must be a single path segment: a crafted `../..` would otherwise let the reader glob
    # outside its run's own tree.
    if runs and not all(valid_run_id(r) for r in runs):
        raise _CoverageError("invalid run id")
    if crawl and not runs:
        # The crawl supplies only the denominator; without runs there is no visited evidence, so the
        # dimension would be a silent 0% rather than a measurement (the CLI warns for the same reason).
        raise _CoverageError("a crawl needs at least one run to know which screens were visited")
    if not runs:
        return report

    artifacts = state.for_org(org).artifacts
    manifests = run_set_manifests(artifacts, runs)
    # The observed-id and screens dimensions both reduce the same per-step `elements.json` set, so
    # read it once: on a server backend each artifact is an object-store GET, and `_artifact_paths`
    # yields one per *step* — reading twice would double a whole run set's fetches just because a
    # crawl was picked.
    element_lists = list(_read_json_lists(artifacts, manifests, "elements"))
    report = dataclasses.replace(
        report,
        endpoints=_coverage.endpoint_coverage(
            scenarios, read_exchanges_via_store(artifacts, manifests)
        ),
        observed=_coverage.observed_id_coverage(
            observed_identifiers(element_lists), eff.id_namespaces
        ),
    )
    if not crawl:
        return report
    return dataclasses.replace(
        report,
        screens=_coverage.screen_coverage(
            discovered_screens_via_store(artifacts, crawl),
            _coverage.screen_fingerprints(element_lists),
        ),
    )


def _runs_from_body(raw: Any) -> list[str]:
    """The run ids a JSON body selected.

    Raises:
        _CoverageError: `runs` is present but not a list. A bare string would iterate into its
            characters and silently compute the wrong (or empty) run set.
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise _CoverageError("runs must be a list of run ids")
    return [str(r) for r in raw]


def _runs_from_query(raw: str | None) -> list[str]:
    """The run ids a query string selected, as one comma-separated value — a query parameter carries
    no list, so `GET /coverage` spells the run set the way a URL can."""
    return [r.strip() for r in (raw or "").split(",") if r.strip()]


def coverage_view(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Aggregate a target's E2E coverage map for the Web UI's Coverage view.

    Loads the target's scenario suite and measures its stable-id references against the app's declared
    ``idNamespaces`` (the static dimension). When ``body['runs']`` names a run set, the endpoint
    (``network.json`` observed vs asserted) and observed-id (``elements.json`` vs declared namespaces)
    dimensions fold in, and ``body['crawl']`` — a crawl run whose ``screenmap.json`` is the discovered
    denominator — adds the screens-visited dimension. Returns the structured figures plus a
    self-contained HTML report the browser renders as-is, so nothing is recomputed (and drifts) in JS.
    """
    try:
        report = _aggregate(
            state,
            str(body.get("target") or ""),
            _runs_from_body(body.get("runs")),
            str(body.get("crawl") or ""),
            actor=actor,
        )
    except _CoverageError as e:
        return {"error": str(e)}, e.status

    payload: dict[str, Any] = {"target": report.target, "static": dataclasses.asdict(report.static)}
    for key, dimension in (
        ("endpoints", report.endpoints),
        ("observed_ids", report.observed),
        ("screens", report.screens),
    ):
        if dimension is not None:
            payload[key] = dataclasses.asdict(dimension)
    payload["html"] = report.html()
    return payload, 200


def coverage_html(
    state: ServeState,
    target: str | None,
    runs: str | None,
    crawl: str | None,
    *,
    actor: str | None = None,
) -> tuple[str, int]:
    """The same coverage map as a linkable page — ``GET /coverage``, the route `/stats`,
    `/flakiness`, and `/usage` each already have (BE-0146).

    *runs* is the comma-separated run set the query string carries (empty for the static dimension
    alone) and *crawl* the crawl run supplying the screens denominator. An input the caller can fix
    renders as an explanatory page carrying that input's status, not a JSON error a browser would
    show raw.
    """
    try:
        report = _aggregate(state, target or "", _runs_from_query(runs), crawl or "", actor=actor)
    except _CoverageError as e:
        return _error_page(str(e)), e.status
    return report.html(), 200


def _error_page(message: str) -> str:
    """A self-contained page explaining why the map couldn't be built — the HTML counterpart of the
    JSON `{"error": ...}` the view's POST returns, so a browser opening `/coverage` directly gets a
    readable answer rather than a raw payload."""
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Coverage</title></head>"
        '<body style="font: 15px/1.5 system-ui, sans-serif; color-scheme: light dark; padding: 2rem">'
        f"<h1>Coverage</h1><p>{html.escape(message)}</p>"
        "<p>Pass <code>?target=&lt;name&gt;</code>, and optionally "
        "<code>&amp;runs=&lt;id&gt;,&lt;id&gt;</code> and <code>&amp;crawl=&lt;id&gt;</code>.</p>"
        "</body></html>"
    )


__all__ = ["coverage_html", "coverage_view"]

"""Cross-project comparison aggregation (BE-0226 unit 1).

Runs the same single-config aggregation BE-0102 already computes (`stats.aggregate_runs`) once per
registered project, over that project's `project_id`-scoped run set, and rolls each result into the
per-project headline the comparison view ranks on. It is a pure, read-only aggregation over stored
run manifests — no device, no network, no model, and never on the `run`/CI verdict path — reusing the
`run_set_manifests` seam (BE-0226 groundwork) to read a run set the registry partitions by project.

The API endpoint (unit 2) and the comparison dashboard (unit 3) build on the model this produces.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from bajutsu.analysis.stats import ProjectMetrics, project_metrics
from bajutsu.serve.artifacts import ArtifactStore
from bajutsu.serve.operations.reads import _STATS_RUN_LIMIT, run_set_manifests
from bajutsu.serve.project_registry import ProjectRegistry
from bajutsu.serve.state import ServeState


def compare_projects(
    registry: ProjectRegistry, store: ArtifactStore, *, org: str
) -> list[ProjectMetrics]:
    """The per-project headline metrics for *org*'s registered projects, in registry order.

    Reads each project's `project_id`-scoped run set from *store* and rolls it up with the shared
    `project_metrics`. Asks the registry for at most `_STATS_RUN_LIMIT` run ids — the same window
    as the single-config dashboard — so the bound is honoured at the source (the DB backend fetches
    only that window rather than the whole history) and a project with a long history stays a
    fixed-cost read. An unrun project charts as a blank row rather than being dropped, so the
    comparison shows the whole registered set.

    Args:
        registry: The project registry to enumerate and partition runs by.
        store: The run-artifact store the manifests are read from.
        org: The org whose projects are compared (`default` locally).

    Returns:
        One `ProjectMetrics` per registered project, ordered as `registry.list_projects` returns
        them (by name).
    """
    rows = []
    for project in registry.list_projects(org_id=org):
        ids = registry.run_ids(org_id=org, project_id=project.id, limit=_STATS_RUN_LIMIT)
        manifests = run_set_manifests(store, ids)
        rows.append(project_metrics(project.id, project.name, manifests))
    return rows


def project_metrics_view(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    """`GET /api/metrics/projects`: the cross-project comparison model as JSON (BE-0226 unit 2).

    One row per registered project — the headline pass-rate, flaky-rate, and duration percentiles the
    ranking sorts on, plus the daily pass-rate trend for a sparkline. Org-scoped through the same seam
    as the hub's own endpoints (resolving to `default` locally), and it returns an empty list when no
    project hub is wired, so a single-config serve reports "nothing to compare" rather than an error.
    Read-only: it re-presents the deterministic verdicts `run` already decided, adding no LLM to the
    path, and sits alongside — not replacing — BE-0102's single-config `/stats` view.
    """
    registry = state.project_registry
    if registry is None:
        return [], 200
    org = state.org_of(actor)
    store = state.for_org(org).artifacts
    return [asdict(row) for row in compare_projects(registry, store, org=org)], 200

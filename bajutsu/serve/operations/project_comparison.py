"""Cross-project comparison aggregation (BE-0226 unit 1).

Runs the same single-config aggregation BE-0102 already computes (`stats.aggregate_runs`) once per
registered project, over that project's `project_id`-scoped run set, and rolls each result into the
per-project headline the comparison view ranks on. It is a pure, read-only aggregation over stored
run manifests — no device, no network, no model, and never on the `run`/CI verdict path — reusing the
`run_set_manifests` seam (BE-0226 groundwork) to read a run set the registry partitions by project.

The API endpoint (unit 2) and the comparison dashboard (unit 3) build on the model this produces.
"""

from __future__ import annotations

from bajutsu.serve.artifacts import ArtifactStore
from bajutsu.serve.operations.reads import _STATS_RUN_LIMIT, run_set_manifests
from bajutsu.serve.project_registry import ProjectRegistry
from bajutsu.stats import ProjectMetrics, project_metrics


def compare_projects(
    registry: ProjectRegistry, store: ArtifactStore, *, org: str
) -> list[ProjectMetrics]:
    """The per-project headline metrics for *org*'s registered projects, in registry order.

    Reads each project's `project_id`-scoped run set from *store* and rolls it up with the shared
    `project_metrics`. Bounded to the same `_STATS_RUN_LIMIT` window as the single-config dashboard
    so a project with a long history stays a fixed-cost read; the registry returns run ids
    newest-first, so the cap keeps the most recent window. An unrun project charts as a blank row
    rather than being dropped, so the comparison shows the whole registered set.

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
        ids = registry.run_ids(org_id=org, project_id=project.id)[:_STATS_RUN_LIMIT]
        manifests = run_set_manifests(store, ids)
        rows.append(project_metrics(project.id, project.name, manifests))
    return rows

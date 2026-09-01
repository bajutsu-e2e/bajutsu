"""Cross-target comparison aggregation (BE-0226 unit 1, repointed to the target axis by BE-0404).

Runs the same single-config aggregation BE-0102 already computes (`stats.aggregate_runs`) once per
target the bound config declares, over that target's run set, and rolls each result into the
per-target headline the comparison view ranks on. It is a pure, read-only aggregation over stored
run manifests — no device, no network, no model, and never on the `run`/CI verdict path — reusing
the `run_set_manifests` seam (BE-0226 groundwork) to read a run set partitioned by `runs.target`.

The axis is the target rather than the project because the target is what a team actually compares:
"the Android target passes while the iOS target fails" is a question about one service, while
"project *checkout* versus project *search*" compared two services nobody runs side by side.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from bajutsu.analysis.stats import TargetMetrics, target_metrics
from bajutsu.serve.helpers import list_targets
from bajutsu.serve.operations.reads import RUN_WINDOW, run_set_manifests
from bajutsu.serve.state import ServeState


def compare_targets(state: ServeState, *, org: str) -> list[TargetMetrics]:
    """The per-target headline metrics for the targets *org* may run, in declaration order.

    The target list comes from the bound config rather than from the run history, so a target
    declared but never run charts as a blank row instead of vanishing — the comparison shows the
    whole set the org may reach, exactly as the project version showed the whole registered one.

    Ownership is resolved through the same `targets_for` seam the target list and the dispatch guard
    use, so a member of one org never sees another org's targets ranked here, nor the run counts
    that would disclose how much that tenant runs. Local serve has no tenant boundary and lists
    every declared target, matching `list_targets_payload`.

    Args:
        state: The serve state holding the bound config and the org-scoped artifact store.
        org: The org whose targets are compared (`default` locally).

    Returns:
        One `TargetMetrics` per target the org may run, ordered as the config declares them.
    """
    if state.binding.config is None:
        return []
    declared = list_targets(state.binding.config)
    if state.repository is not None:
        owned = set(state.targets_for(org))
        declared = [t for t in declared if t in owned]
    artifacts = state.for_org(org).artifacts
    rows = []
    for target in declared:
        ids = _target_run_ids(state, org=org, target=target)
        rows.append(target_metrics(target, run_set_manifests(artifacts, ids)))
    return rows


def _target_run_ids(state: ServeState, *, org: str, target: str) -> list[str]:
    """The newest `RUN_WINDOW` run ids for *target*, honoured at the source where possible.

    With a repository the bound is pushed into the query (`runs.target` is an ordinary column), so a
    target with a long history stays a fixed-cost read. Without one the artifact store's own listing
    carries the manifest's `target` stamp, so the same partition is a post-filter over the same
    window — the local stand-in, as the run list's own label filter is.
    """
    if state.repository is not None:
        return [
            r.id for r in state.repository.list_runs(org_id=org, target=target, limit=RUN_WINDOW)
        ]
    listing = state.for_org(org).artifacts.list_runs()
    return [str(r.get("id")) for r in listing if r.get("target") == target][:RUN_WINDOW]


def target_metrics_view(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    """`GET /api/metrics/targets`: the cross-target comparison model as JSON (BE-0226 unit 2).

    One row per declared target — the headline pass-rate, flaky-rate, and duration percentiles the
    ranking sorts on, plus the daily pass-rate trend for a sparkline. Org-scoped through the same
    seam as the rest of serve's reads (resolving to `default` locally), and it returns an empty list
    when no config is bound, so a serve with nothing open reports "nothing to compare" rather than
    an error. Read-only: it re-presents the deterministic verdicts `run` already decided, adding no
    LLM to the path, and sits alongside — not replacing — BE-0102's single-config `/stats` view.
    """
    return [asdict(row) for row in compare_targets(state, org=state.org_of(actor))], 200

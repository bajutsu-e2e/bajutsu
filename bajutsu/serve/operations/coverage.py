"""Coverage-map serve operation (BE-0146).

Surfaces the deterministic `bajutsu coverage` aggregation (BE-0050) in the serve Web UI: the static
id-namespace dimension always, and — when a run set is selected — the endpoints-observed-vs-asserted
and observed-id dimensions folded in from that run set's captured artifacts. Read-only,
deterministic, AI-free: every figure is a count over declared namespaces and `network.json` /
`elements.json`, never a verdict and never a gate.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from bajutsu import coverage as _coverage
from bajutsu.config import load_config, resolve
from bajutsu.scenario import load_scenarios_dir
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.jobs import ServeState, _scenarios_dir_for


def coverage_view(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Aggregate a target's E2E coverage map for the Web UI's Coverage view.

    Loads the target's scenario suite and measures its stable-id references against the app's declared
    ``idNamespaces`` (the static dimension). When ``body['runs']`` names a run set, the endpoint
    (``network.json`` observed vs asserted) and observed-id (``elements.json`` vs declared namespaces)
    dimensions fold in. Returns the structured figures plus a self-contained HTML report the browser
    renders as-is — the aggregation stays server-side, so nothing is recomputed (and drifts) in JS.
    """
    if state.config is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    target = str(body["target"])

    config = load_config(state.config.read_text(encoding="utf-8"))
    if target not in config.targets:
        return {"error": f"unknown target: {target}"}, 400

    scenarios_dir = _scenarios_dir_for(state, target)
    if scenarios_dir is None or not scenarios_dir.is_dir():
        return {"error": f"target '{target}' has no scenarios dir"}, 400
    try:
        scenarios = load_scenarios_dir(scenarios_dir)
    except (OSError, ValueError) as e:
        return {"error": f"failed to load scenarios: {e}"}, 400

    eff = resolve(config, target)
    static = _coverage.coverage(scenarios, eff.id_namespaces)

    # A run set (optional) folds in the run-evidence dimensions. Every id must be a single path
    # segment — a crafted `../..` would otherwise let the reader glob outside its run's own tree.
    runs = [str(r) for r in body.get("runs") or []]
    if runs and not all(valid_run_id(r) for r in runs):
        return {"error": "invalid run id"}, 400
    endpoints = None
    observed = None
    if runs:
        endpoints = _coverage.endpoint_coverage(
            scenarios, _coverage.read_exchanges(state.runs_dir, run_ids=runs)
        )
        observed = _coverage.observed_id_coverage(
            _coverage.read_observed_ids(state.runs_dir, run_ids=runs), eff.id_namespaces
        )

    payload: dict[str, Any] = {"target": target, "static": dataclasses.asdict(static)}
    if endpoints is not None:
        payload["endpoints"] = dataclasses.asdict(endpoints)
    if observed is not None:
        payload["observed_ids"] = dataclasses.asdict(observed)
    payload["html"] = _coverage.render_html(
        static, endpoints=endpoints, observed=observed, target=target
    )
    return payload, 200


__all__ = ["coverage_view"]

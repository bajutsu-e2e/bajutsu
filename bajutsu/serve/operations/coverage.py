"""Coverage-map serve operation (BE-0146).

Surfaces the deterministic `bajutsu coverage` aggregation (BE-0050) in the serve Web UI: the static
id-namespace dimension always, and — when a run set is selected — the endpoints-observed-vs-asserted
and observed-id dimensions folded in from that run set's captured artifacts. Read-only,
deterministic, AI-free: every figure is a count over declared namespaces and `network.json` /
`elements.json`, never a verdict and never a gate.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from typing import Any

from bajutsu.analysis import coverage as _coverage
from bajutsu.config import load_config, resolve
from bajutsu.evidence.network import NetworkExchange
from bajutsu.scenario import load_scenarios_dir
from bajutsu.serve.artifacts import ArtifactStore
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
    discipline `bajutsu.analysis.coverage.read_exchanges`/`read_observed_ids` apply to a local `runs_dir`.
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


def read_observed_ids_via_store(store: ArtifactStore, manifests: list[dict[str, Any]]) -> list[str]:
    """`bajutsu.analysis.coverage.read_observed_ids`'s seam-routed counterpart: every stable id across
    *manifests* (e.g. from `run_set_manifests`), reading each step's ``elements.json`` through
    *store* instead of globbing a local ``runs_dir`` (BE-0258)."""
    return [
        e["identifier"]
        for data in _read_json_lists(store, manifests, "elements")
        for e in data
        if isinstance(e, dict) and isinstance(e.get("identifier"), str) and e["identifier"]
    ]


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

    # A run set (optional) folds in the run-evidence dimensions. Require an actual JSON list — a bare
    # string would iterate into its characters and silently compute the wrong (or empty) run set.
    # Every id must then be a single path segment: a crafted `../..` would otherwise let the reader
    # glob outside its run's own tree.
    raw_runs = body.get("runs") or []
    if not isinstance(raw_runs, list):
        return {"error": "runs must be a list of run ids"}, 400
    runs = [str(r) for r in raw_runs]
    if runs and not all(valid_run_id(r) for r in runs):
        return {"error": "invalid run id"}, 400
    endpoints = None
    observed = None
    if runs:
        artifacts = state.for_org(state.org_of(actor)).artifacts
        manifests = run_set_manifests(artifacts, runs)
        endpoints = _coverage.endpoint_coverage(
            scenarios, read_exchanges_via_store(artifacts, manifests)
        )
        observed = _coverage.observed_id_coverage(
            read_observed_ids_via_store(artifacts, manifests), eff.id_namespaces
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

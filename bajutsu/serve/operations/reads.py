"""Scenario / run-artifact read serve operations (BE-0127)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from bajutsu import stats as _stats
from bajutsu.config import Config, load_config, targets_for_org
from bajutsu.drivers import base as driver_base
from bajutsu.scenario import load_scenario_file
from bajutsu.scenario.models import STEP_ACTIONS, Step
from bajutsu.serve import jobs
from bajutsu.serve.artifacts import Artifact, ArtifactStore
from bajutsu.serve.authz import _target_forbidden
from bajutsu.serve.helpers import (
    list_fs,
    list_simulators,
    list_targets,
    load_config_file,
    valid_run_id,
    valid_scenario_ref,
)
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.operations._common import _resolve_org_or_forbid
from bajutsu.serve.operations.config import FS_DISABLED_ERROR

_REPORT_SUFFIX = "/report.html"


def run_file(store: ArtifactStore, rel: str) -> Artifact | None:
    """Serve a run-relative artifact, rendering `report.html` **on view** (BE-0068).

    For `<run_id>/report.html` the report is rendered fresh from the stored model with the current
    template (`store.render_report`), falling back to the baked file when the model can't be loaded;
    any other artifact (screenshots, videos, manifest.json, …) is served byte-for-byte.
    """
    if rel.endswith(_REPORT_SUFFIX):
        # `render_report` validates + confines the run id itself (returning None for a non-run or a
        # nested path), so containment stays in one place and we fall back to the baked file via get.
        rendered = store.render_report(rel[: -len(_REPORT_SUFFIX)])
        if rendered is not None:
            return rendered
    return store.get(rel)


def list_scenarios(
    state: ServeState, target: str | None, *, actor: str | None = None
) -> tuple[Any, int]:
    # Hide a target that belongs to another org (non-leaky: an empty list, not a 403) — BE-0015
    # multi-tenancy. The scenarios come from the actor's org-scoped store.
    org = state.org_of(actor)
    if target is not None and _target_forbidden(state, org, target):
        return [], 200
    scope = state.for_org(org).scenarios.scope(target)
    return (scope.list() if scope else []), 200


def _primary_backend(config: Config, name: str) -> str:
    """The target's first (effective) backend token, so the Web UI can tell web from iOS apps."""
    target = config.targets.get(name)
    if target is None:
        return ""
    backends = target.backend or config.defaults.backend
    return backends[0] if backends else ""


def list_targets_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    # Each target carries its primary backend, so selecting a web target can hide the iOS-only controls
    # (and show the headed toggle) without the user typing the backend by hand.
    if state.config is None:
        return [], 200
    config = load_config_file(state.config)
    if config is None:
        return [], 200
    # Org scoping applies only on a server backend with a system of record; local serve / token mode
    # ignores `orgs:` and lists every target (BE-0015 multi-tenancy).
    if state.repository is None:
        names = list_targets(state.config)
    else:
        names = sorted(targets_for_org(config, state.org_of(actor)))
    return [{"name": n, "backend": _primary_backend(config, n)} for n in names], 200


def browse_fs(state: ServeState, sub: str | None) -> tuple[Any, int]:
    if state.hosted:
        # The file browser is a local affordance; a hosted deployment removes it from the UI and
        # refuses it here too, so a hand-crafted request can't list the operator's --root (BE-0108).
        return {"error": FS_DISABLED_ERROR}, 403
    try:
        return list_fs(state.root, sub), 200
    except (ValueError, OSError) as e:
        return {"error": str(e)}, 400


def simulators_payload(state: ServeState) -> tuple[Any, int]:
    return list_simulators(state.simctl), 200


def runs_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    # With a system of record (server backend), the history is the actor's org's recorded runs —
    # durable and org-scoped (BE-0015 7c-4). The stored summary mirrors the artifact entry, so the
    # UI shape is identical. Without one (local / stdlib serve), list straight from the artifact
    # store.
    if state.repository is not None:
        return [r.summary for r in state.repository.list_runs(org_id=state.org_of(actor))], 200
    return state.artifacts.list_runs(), 200


# The newest-N run window a serve `/stats` refresh aggregates. Bounds the per-refresh manifest reads
# over object storage, and keeps the DB and artifact-store paths aggregating the same set (the DB
# `list_runs` is itself limit-bounded). A large enough window to read a trend, not the whole history.
_STATS_RUN_LIMIT = 200


def stats_html(state: ServeState, *, actor: str | None = None) -> tuple[str, int]:
    """The aggregate run-stats dashboard (BE-0102) as a self-contained HTML page, org-scoped.

    Reuses the deterministic aggregator over the actor's org run history: read-only, no verdict, no
    LLM. The run-id list comes from the same seam as `runs_payload` (the system of record when wired,
    else the artifact store); each run's full `manifest.json` is read from the artifact store either
    way, since the DB `summary` carries only the compact history-list shape.
    """
    return _stats.render_html(_stats.aggregate_runs(_run_manifests(state, actor))), 200


def _run_manifests(state: ServeState, actor: str | None) -> list[dict[str, Any]]:
    """The newest runs' parsed `manifest.json` for the actor's org; unreadable/malformed ones skipped.

    The ids come from the recorded runs when a repository is wired (org-scoped), else the artifact
    store's own listing; both are newest-first and bounded to the same `_STATS_RUN_LIMIT` window so a
    `/stats` refresh over a large history stays cheap and the two backends aggregate the same set. The
    manifests are always read from the org's artifact store — the seam that holds the full manifest
    whether or not a database indexes the runs — keyed by the canonical run id.
    """
    org = state.org_of(actor)
    artifacts = state.for_org(org).artifacts
    ids: list[Any]
    if state.repository is not None:
        ids = [r.id for r in state.repository.list_runs(org_id=org, limit=_STATS_RUN_LIMIT)]
    else:
        ids = [r.get("id") for r in artifacts.list_runs()[:_STATS_RUN_LIMIT]]
    manifests: list[dict[str, Any]] = []
    for run_id in ids:
        # Reject a non-string or a multi-segment id (e.g. "r1/sub") before it becomes a path, matching
        # serve's containment model for run ids everywhere else (BE-0015).
        if not isinstance(run_id, str) or not valid_run_id(run_id):
            continue
        try:
            # `open_bytes` can raise (a run deleted between listing and read; a remote store's I/O
            # error), so an OSError is a skip too — the same "unreadable ones are skipped" promise as
            # malformed JSON, never a failed dashboard.
            raw = artifacts.open_bytes(f"{run_id}/manifest.json")
            data = json.loads(raw) if raw is not None else None
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            manifests.append(data)
    return manifests


def read_scenario(
    state: ServeState,
    target: str | None,
    path: str | None,
    *,
    actor: str | None = None,
    run_id: str | None = None,
    scenario_name: str | None = None,
) -> tuple[Any, int]:
    # A scenario in another org's target reads as not-found (non-leaky) — BE-0015 multi-tenancy.
    org = state.org_of(actor)
    if target is not None and _target_forbidden(state, org, target):
        return {"error": "not found"}, 404
    scope = state.for_org(org).scenarios.scope(target)
    text = scope.read(path) if scope else None
    if text is None:
        return {"error": "not found"}, 404
    if not run_id:
        return {"yaml": text}, 200
    if not valid_run_id(run_id):
        return {"yaml": text, "steps": []}, 200
    return {"yaml": text, "steps": _step_artifacts(state, text, run_id, scenario_name)}, 200


def _step_artifacts(
    state: ServeState,
    yaml_text: str,
    run_id: str,
    scenario_name: str | None,
) -> list[dict[str, Any]]:
    """Build per-step artifact handles for the editor (BE-0013)."""
    try:
        scenarios = load_scenario_file(yaml_text).scenarios
    except (ValueError, Exception):
        return []

    manifest_path = state.runs_dir / run_id / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    matched = (
        next((s for s in scenarios if s.name == scenario_name), None) if scenario_name else None
    )
    if matched is None and scenarios:
        matched = scenarios[0]
    if matched is None:
        return []

    effective_name = scenario_name or (matched.name if matched else None)
    sid = _find_sid(manifest, effective_name)
    if sid is None:
        return []

    result: list[dict[str, Any]] = []
    for idx, step in enumerate(matched.steps):
        step_id = f"{sid}/{step.name or f'step{idx}'}"
        step_dir = state.runs_dir / run_id / step_id
        elements_file = step_dir / "elements.json"
        screenshot_file = step_dir / "after.png"
        action, fields = _step_action_fields(step)
        result.append(
            {
                "stepId": step_id,
                "action": action,
                "fields": fields,
                "elementsUrl": f"/runs/{run_id}/{step_id}/elements.json"
                if elements_file.is_file()
                else None,
                "screenshotUrl": f"/runs/{run_id}/{step_id}/after.png"
                if screenshot_file.is_file()
                else None,
            }
        )
    return result


def _step_action_fields(step: Step) -> tuple[str, Any]:
    """Extract the action kind and its fields from a parsed Step.

    Fields may be a dict (tap, type, …) or a list (assert).
    """
    dumped = step.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
    for field_name in STEP_ACTIONS:
        alias = Step.model_fields[field_name].alias or field_name
        if alias in dumped:
            return alias, dumped[alias]
    return "unknown", {}


def _valid_step_id(step_id: str) -> bool:
    """Whether *step_id* is a safe relative path (no traversal, no absolute)."""
    if not step_id or step_id.startswith("/"):
        return False
    parts = Path(step_id).parts
    return ".." not in parts


def _find_sid(manifest: dict[str, Any], scenario_name: str | None) -> str | None:
    """Find the evidence-dir slug for *scenario_name* in the manifest."""
    for scn in manifest.get("scenarios", []):
        if scn.get("scenario") == scenario_name:
            return scn.get("sid") or None
    return None


def job_view(state: ServeState, job_id: str) -> tuple[Any, int]:
    job = state.jobs.get(job_id)
    if job is None:
        return {"error": "no such job"}, 404
    view = job.view()
    # Locally the job ran in-process, so its own view (with the log buffer) is authoritative. On the
    # server backend it ran on a worker and the control-plane Job stays "running"; fall back to the
    # bus's terminal status then (BE-0015 W2).
    if view["status"] != "done":
        final = state.logbus.final(job_id)
        if final is not None:
            return json.loads(final), 200
    return view, 200


def save_scenario(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Save an edited scenario back to its ``*.yaml`` (bounded to the target's scenarios dir)."""
    target = str(body.get("target") or "") or None
    org = state.org_of(actor)
    # Deny saving into another org's target (BE-0015 multi-tenancy); single-tenant never forbids.
    if target is not None and _target_forbidden(state, org, target):
        return {"error": "forbidden"}, 403
    # Resolve the scope and screen the ref before parsing: a non-saveable path is reported ahead of
    # a YAML error (the local store passes an absolute path inside its dir).
    scope = state.for_org(org).scenarios.scope(target)
    ref = body.get("path")
    ref = ref if isinstance(ref, str) else None
    if scope is None or not valid_scenario_ref(ref, allow_absolute=True):
        return {"error": "path must be a *.yaml under the scenarios dir"}, 400
    text = str(body.get("yaml", ""))
    try:
        load_scenario_file(text)
    except (ValueError, OSError, yaml.YAMLError) as e:
        return {"error": f"invalid scenario: {e}"}, 400
    saved = scope.save(ref, text)
    if saved is None:
        return {"error": "path must be a *.yaml under the scenarios dir"}, 400
    return {"ok": True, "path": saved}, 200


def approve_baseline(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Promote a run's captured screenshot to a `visual` baseline.

    Reads ``runs/<runId>/<sid>/visual-actual.png`` from the artifact store and writes it as baseline
    *baseline* via the baseline store — both seams confine the name to their root (filesystem dir or
    object-storage prefix), so a crafted runId / sid / baseline can't read or write outside it. Both
    are scoped to the actor's org, so a run in another org reads as not-found (BE-0015)."""
    run_id = str(body.get("runId") or "")
    sid = str(body.get("sid") or "")
    baseline = str(body.get("baseline") or "")
    if not run_id or not sid or not baseline:
        return {"error": "runId, sid and baseline are required"}, 400
    bundle = state.for_org(state.org_of(actor))
    data = bundle.artifacts.open_bytes(f"{run_id}/{sid}/visual-actual.png")
    if data is None:
        return {"error": "no captured screenshot for this run"}, 404
    if bundle.baselines.write(baseline, data) is None:
        return {"error": "invalid baseline name"}, 400
    return {"ok": True, "baseline": baseline}, 200


def cancel_job(state: ServeState, job_id: str) -> tuple[Any, int]:
    job = state.jobs.get(job_id)
    if job is None:
        return {"error": "no such job"}, 404
    return {"cancelled": jobs.cancel_job(job)}, 200


def resolve_scenario_pick(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Resolve a point against a step's stored elements.json — no live driver."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400

    target = str(body.get("target", ""))
    run_id = str(body.get("runId", ""))
    step_id = str(body.get("stepId", ""))
    raw_point = body.get("point")

    if not target:
        return {"error": "target is required"}, 400
    if not run_id or not valid_run_id(run_id):
        return {"error": "invalid or missing runId"}, 400
    if not step_id or not _valid_step_id(step_id):
        return {"error": "invalid or missing stepId"}, 400
    if not isinstance(raw_point, list) or len(raw_point) != 2:
        return {"error": "point must be [x, y] normalized"}, 400
    try:
        nx, ny = float(raw_point[0]), float(raw_point[1])
    except (TypeError, ValueError):
        return {"error": "point must be [x, y] normalized"}, 400

    _org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400
    namespaces: list[str] = list(target_cfg.id_namespaces)

    elements_path = state.runs_dir / run_id / step_id / "elements.json"
    if not elements_path.is_file():
        return {"error": "elements.json not found for this step"}, 404

    try:
        raw = json.loads(elements_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return {"error": "elements.json is not a valid element list"}, 400
        elements: list[driver_base.Element] = [
            {
                "identifier": el.get("identifier"),
                "label": el.get("label"),
                "traits": list(el.get("traits", [])),
                "value": el.get("value"),
                "frame": tuple(el.get("frame", (0, 0, 0, 0))),
            }
            for el in raw
        ]
    except (json.JSONDecodeError, OSError, AttributeError, TypeError):
        return {"error": "elements.json is corrupt or unreadable"}, 400

    from bajutsu.elements import screen_size_from_elements
    from bajutsu.record_capture import resolve_capture

    sw, sh = screen_size_from_elements(elements)
    px, py = nx * sw, ny * sh
    result = resolve_capture(elements, (px, py), namespaces)

    if result.refused:
        return {"refused": result.refused}, 200
    if result.ambiguity:
        return {
            "ambiguous": True,
            "selector": result.selector.model_dump(exclude_none=True),
            "rung": result.rung,
            "candidates": len(result.ambiguity),
        }, 200
    return {
        "selector": result.selector.model_dump(exclude_none=True),
        "rung": result.rung,
    }, 200

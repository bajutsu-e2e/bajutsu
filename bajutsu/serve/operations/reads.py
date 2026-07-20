"""Scenario / run-artifact read serve operations (BE-0127)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from bajutsu import handoff
from bajutsu.analysis import stats as _stats
from bajutsu.analytics import ledger as _usage_ledger
from bajutsu.analytics import stats as _usage_stats
from bajutsu.config import Config, load_config
from bajutsu.drivers import base as driver_base
from bajutsu.scenario import load_scenario_file
from bajutsu.scenario.models import STEP_ACTIONS, Scenario, Step
from bajutsu.serve import flakiness as _flakiness
from bajutsu.serve import jobs
from bajutsu.serve.artifacts import Artifact, ArtifactStore
from bajutsu.serve.authz import _target_forbidden
from bajutsu.serve.helpers import (
    list_fs,
    list_simulators,
    list_targets,
    load_serve_config_file,
    valid_run_id,
    valid_scenario_ref,
)
from bajutsu.serve.operations._common import _resolve_org_or_forbid
from bajutsu.serve.operations.config import FS_DISABLED_ERROR
from bajutsu.serve.operations.runs import sweep_expired_trash
from bajutsu.serve.orgs import targets_for_org
from bajutsu.serve.server.db import DEFAULT_RUN_LIMIT as _RUN_HISTORY_LIMIT
from bajutsu.serve.state import ServeState

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
    # Each target carries its primary backend, so the UI shows only that platform's device controls
    # (iOS controls, or the web headed toggle) without the user typing the backend by hand.
    if state.config is None:
        return [], 200
    parsed = load_serve_config_file(state.config)
    if parsed is None:
        return [], 200
    config, orgs = parsed
    # Org scoping applies only on a server backend with a system of record; local serve / token mode
    # ignores `orgs:` and lists every target (BE-0015 multi-tenancy).
    if state.repository is None:
        names = list_targets(state.config)
    else:
        names = sorted(targets_for_org(orgs, config.targets, state.org_of(actor)))
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


# The newest-N run window the history list shows: `db.DEFAULT_RUN_LIMIT`, the DB `list_runs` default
# cap, sourced so the two stay in lock-step. Also the post-filter cap for a scenario-scoped DB list,
# so a scoped picker on the server backend stays as bounded as the unscoped one.


def runs_payload(
    state: ServeState, *, actor: str | None = None, scenario: str | None = None
) -> tuple[Any, int]:
    # Opportunistically purge trash past the retention window before listing (BE-0239) — the lazy
    # sweep, on the history read rather than a background daemon (SqlSessionStore's expiry-on-read
    # precedent). A no-op when retention is disabled; scoped to the actor's org.
    sweep_expired_trash(state, actor=actor)
    # With a system of record (server backend), the history is the actor's org's recorded runs —
    # durable and org-scoped (BE-0015 7c-4). The stored summary mirrors the artifact entry, so the
    # UI shape is identical. Without one (local / stdlib serve), list straight from the artifact
    # store.
    #
    # When scoping to a scenario, the DB cap must count *scoped* runs, not global ones: the scenario
    # name lives in the JSON summary, not an indexed column, so it can't push into the query — list
    # unbounded and cap after filtering, or a run of the loaded scenario that falls outside the
    # newest-N global window is silently dropped and the picker can't reach it (BE-0262 follow-up).
    if state.repository is not None:
        limit = None if scenario is not None else _RUN_HISTORY_LIMIT
        runs = [
            r.summary for r in state.repository.list_runs(org_id=state.org_of(actor), limit=limit)
        ]
    else:
        runs = state.artifacts.list_runs()
    # Scope the Author run picker to the loaded scenario (BE-0262): a chosen run's step ids only line
    # up with a scenario of the same name, so a run that never executed it can't feed the picker.
    # Scenario name is the step-id compatibility key the run-backed resolve already keys on (a run's
    # summary records the names it ran, not a file path); org scoping and the target-scoped Author
    # scenario list bound the rest. On the DB path, re-cap the scoped list to the same newest-N window
    # so the payload stays bounded like the unscoped one (list_runs returns newest-first). The local
    # artifact-store list is unbounded either way, so re-capping it would make scoped *stricter* than
    # unscoped — gate the re-cap to the DB path so local/dev serve stays symmetric.
    if scenario is not None:
        runs = [r for r in runs if scenario in (r.get("scenarios") or [])]
        if state.repository is not None:
            runs = runs[:_RUN_HISTORY_LIMIT]
    return runs, 200


def crawl_runs_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    """Past crawl runs for the Crawl tab's history list, from the actor's org store (BE-0180/BE-0190).

    Keyed on screenmap.json (the artifact every crawl streams), separate from `runs_payload`'s
    manifest-backed pass/fail history — a crawl run has no such verdict. Read-only and AI-free: it
    only summarizes the deterministic screen map and links to the crash/flow scenario files the crawl
    already wrote, served through the existing `/runs/<id>/...` static mount.

    Listed through the actor's org-scoped `ArtifactStore`, exactly as `runs_payload` and `/runs/<id>/...`
    are (BE-0190): the local backend resolves to the default org's `LocalArtifactStore` (a `runs_dir`
    scan, today's behavior), while a server backend reads the org's object store, so the history is
    tenant-scoped by construction — no run id from another org is reachable.
    """
    sweep_expired_trash(state, actor=actor)  # lazy retention purge before listing (BE-0239)
    return state.for_org(state.org_of(actor)).artifacts.list_crawl_runs(), 200


def trashed_runs_payload(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    """Soft-deleted runs for the Web UI's Trash view (BE-0239), org-scoped like every history read.

    Each entry is ``{"id", "deletedAt"}`` from the actor's org store — the same trash the retention
    sweep reads, so a run that a normal delete tombstoned (store + DB together) is listed here. The
    sweep runs first, so a run already past the retention window never shows as restorable. Read-only
    and AI-free: it lists what a human soft-deleted, deciding no verdict.
    """
    sweep_expired_trash(
        state, actor=actor
    )  # drop expired trash before listing, as runs_payload does
    return state.for_org(state.org_of(actor)).artifacts.list_trashed_runs(), 200


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
    # live=True: this is the serve /stats view, so the day/backend/hotspot cells render as drilldown
    # deep links into the SPA's run history (BE-0241); the CLI --html export leaves them plain text.
    return _stats.render_html(_stats.aggregate_runs(_run_manifests(state, actor)), live=True), 200


def flakiness_html(state: ServeState, *, actor: str | None = None) -> tuple[str, int]:
    """The ranked flaky-scenario panel (BE-0220, Half 1) as a self-contained HTML page, org-scoped.

    Ranks the actor's org run history by how much each scenario's verdict flips at a constant
    content fingerprint. When a repository is wired the records come straight from it — the
    provenance stamp the BE-0220 prerequisite added to the run row is the grouping key, so no
    manifest re-read is needed; without one (local / stdlib serve) the same records are built from
    each run's `manifest.json`. Read-only and AI-free: it displays the ranking, deciding no verdict.
    """
    return _flakiness.render_html(_flakiness_report(state, actor)), 200


def _flakiness_report(state: ServeState, actor: str | None) -> _flakiness.FlakinessReport:
    """Rank the actor's org run history — from the DB provenance stamp when wired, else manifests."""
    org = state.org_of(actor)
    if state.repository is not None:
        records = state.repository.list_runs(org_id=org, limit=_flakiness.DEFAULT_RUN_LIMIT)
    else:
        records = _flakiness.records_from_manifests(_run_manifests(state, actor))
    return _flakiness.rank_flakiness(records)


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
    return run_set_manifests(artifacts, ids)


def run_set_manifests(store: ArtifactStore, run_ids: Iterable[Any]) -> list[dict[str, Any]]:
    """Read the parsed `manifest.json` of each run in *run_ids* from *store*, skipping bad ones.

    The `ServeState`/actor-free core of `_run_manifests`: it takes the run set explicitly, so the
    same aggregation can be run once per project over a project-scoped run set (BE-0226) rather than
    only over the active config's org run history. An id that is not a single safe segment is
    rejected before it becomes a path (serve's containment model, BE-0015), and an unreadable or
    malformed manifest is skipped — the aggregator never fails on one bad run. Each manifest already
    carries its own `runId` (`bajutsu.report.manifest.manifest_dict`), so a caller that needs to
    rebuild a run-relative path (e.g. `coverage_view`'s seam-routed evidence readers, BE-0258) reads
    it back from there rather than needing the id threaded through separately.
    """
    manifests: list[dict[str, Any]] = []
    for run_id in run_ids:
        # Reject a non-string or a multi-segment id (e.g. "r1/sub") before it becomes a path, matching
        # serve's containment model for run ids everywhere else (BE-0015).
        if not isinstance(run_id, str) or not valid_run_id(run_id):
            continue
        try:
            raw = store.open_bytes(f"{run_id}/manifest.json")
            data = json.loads(raw) if raw is not None else None
        except (OSError, json.JSONDecodeError, ValueError):
            # `open_bytes` can raise (a run deleted between listing and read; a remote store's I/O
            # error), so an OSError is a skip too — the same "unreadable ones are skipped" promise as
            # malformed JSON, never a failed dashboard.
            continue
        if isinstance(data, dict):
            manifests.append(data)
    return manifests


def usage_html(state: ServeState, *, actor: str | None = None) -> tuple[str, int]:
    """The AI usage/cost dashboard (BE-0195) as a self-contained HTML page.

    Reads the same attributed ledger the serve process's AI subprocesses append to and aggregates it
    deterministically: read-only, no verdict, no LLM. A disabled or absent ledger is not an error —
    it aggregates to the empty state, which explains how recording is enabled (graceful degradation,
    like the readiness panels). The ledger is a single per-process file, not org-scoped, so the view
    is not filtered by *actor* (a per-org ledger would follow the ledger becoming org-scoped).
    """
    path = _usage_ledger_path(state)
    try:
        events = _usage_ledger.read_events(path) if path is not None else []
    except OSError:
        # An unreadable ledger (a permission issue, a transient I/O error) degrades to the empty-state
        # dashboard rather than a 500 — the same "skip what can't be read" promise `/stats` makes.
        events = []
    return _usage_stats.render_html(_usage_stats.aggregate_usage(events)), 200


def _usage_ledger_path(state: ServeState) -> Path | None:
    """The ledger file the dashboard reads — resolved exactly as the AI subprocesses resolve it.

    AI work in serve runs as subprocesses that call `usage_ledger.configure_from_ai_config`, writing
    to the team-wide `defaults.ai.usageLedger` (else `DEFAULT_LEDGER_PATH`) relative to their cwd
    (`state.cwd`). The dashboard points at that same file. An explicit empty string disables
    persistence — None then, so there is nothing to read.
    """
    loaded = load_serve_config_file(state.config)  # cached parse; None when absent/unreadable
    ai = loaded[0].defaults.ai if loaded is not None else None
    path = _usage_ledger.resolve_ledger_path(ai.usage_ledger if ai is not None else None)
    if path is None or path.is_absolute():
        return path
    return state.cwd / path


def read_scenario(
    state: ServeState,
    target: str | None,
    path: str | None,
    *,
    actor: str | None = None,
    run_id: str | None = None,
    scenario_name: str | None = None,
    structure: bool = False,
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
        # The Replay viewer (BE-0273) opts in with `structure` to read what a scenario *is* without
        # a run: the runner's own per-scenario parse, no run-scoped URLs.
        if structure:
            return {"yaml": text, "scenarios": _scenario_structure(text)}, 200
        # No run selected: the step list is derived from the YAML alone so the Author Edit picker
        # works on a scenario that has never run — a live session supplies the screenshot (BE-0262).
        return {"yaml": text, "steps": _yaml_steps(text, scenario_name)}, 200
    if not valid_run_id(run_id):
        return {"yaml": text, "steps": []}, 200
    return {"yaml": text, "steps": _step_artifacts(state, text, run_id, scenario_name, org)}, 200


def _parse_scenarios_safe(yaml_text: str) -> list[Scenario]:
    """The file's scenarios via the runner's parse, or an empty list if it won't parse.

    Both read paths that surface a scenario's structure without a run (the editor's step
    artifacts and the Replay viewer) treat an unparseable file as "nothing to show" rather
    than an error, so they share this swallow.
    """
    try:
        return load_scenario_file(yaml_text).scenarios
    except Exception:
        return []


def _scenario_structure(yaml_text: str) -> list[dict[str, Any]]:
    """Every named scenario in the file with its ordered steps, from the runner's own parse.

    This is the read-only Replay viewer's structured view (BE-0273): it reuses the runner's
    `Step` model (`_step_action_fields`) rather than reparsing in the browser, so it can never
    drift from how a run actually reads the scenario. Unparseable YAML yields an empty list —
    the viewer falls back to the raw text, which stays authoritative.
    """
    result: list[dict[str, Any]] = []
    for scenario in _parse_scenarios_safe(yaml_text):
        steps = []
        for step in scenario.steps:
            action, fields = _step_action_fields(step)
            steps.append({"action": action, "fields": fields})
        result.append({"name": scenario.name, "description": scenario.description, "steps": steps})
    return result


def _matched_scenario(yaml_text: str, scenario_name: str | None) -> Scenario | None:
    """The named scenario in the YAML (else the first), or None if it doesn't parse or is empty.

    The one place the editor resolves "which scenario in this file" — shared by the run-backed step
    list and the run-less, YAML-derived one so both pick the same scenario.
    """
    scenarios = _parse_scenarios_safe(yaml_text)
    matched = (
        next((s for s in scenarios if s.name == scenario_name), None) if scenario_name else None
    )
    if matched is None and scenarios:
        matched = scenarios[0]
    return matched


def _yaml_steps(yaml_text: str, scenario_name: str | None) -> list[dict[str, Any]]:
    """Step handles derived from the scenario YAML alone — no run artifacts (BE-0262).

    The Author Edit picker needs a step list for a scenario that has never run, so a live session
    can target a step to fix. Screenshot/elements URLs are None because there is no stored run; the
    live path supplies the current screenshot.
    """
    matched = _matched_scenario(yaml_text, scenario_name)
    if matched is None:
        return []
    return [
        {
            "stepId": None,
            "action": action,
            "fields": fields,
            "elementsUrl": None,
            "screenshotUrl": None,
        }
        for action, fields in (_step_action_fields(step) for step in matched.steps)
    ]


def _step_artifacts(
    state: ServeState,
    yaml_text: str,
    run_id: str,
    scenario_name: str | None,
    org: str,
) -> list[dict[str, Any]]:
    """Build per-step artifact handles for the editor (BE-0013)."""
    matched = _matched_scenario(yaml_text, scenario_name)
    if matched is None:
        return []

    artifacts = state.for_org(org).artifacts
    try:
        raw_manifest = artifacts.open_bytes(f"{run_id}/manifest.json")
        manifest = json.loads(raw_manifest) if raw_manifest is not None else None
    except (OSError, json.JSONDecodeError):
        # A race (the run trashed/purged between listing and read) or a transient store error reads
        # the same as a missing/malformed manifest — an empty step list, never a failed request.
        return []
    if manifest is None:
        return []

    effective_name = scenario_name or matched.name
    sid = _find_sid(manifest, effective_name)
    if sid is None:
        return []

    result: list[dict[str, Any]] = []
    for idx, step in enumerate(matched.steps):
        step_id = f"{sid}/{step.name or f'step{idx}'}"
        action, fields = _step_action_fields(step)
        result.append(
            {
                "stepId": step_id,
                "action": action,
                "fields": fields,
                "elementsUrl": f"/runs/{run_id}/{step_id}/elements.json"
                if _safe_exists(artifacts, f"{run_id}/{step_id}/elements.json")
                else None,
                "screenshotUrl": f"/runs/{run_id}/{step_id}/after.png"
                if _safe_exists(artifacts, f"{run_id}/{step_id}/after.png")
                else None,
            }
        )
    return result


def _safe_exists(store: ArtifactStore, rel: str) -> bool:
    """`store.exists(rel)`, treating a store I/O error as absent rather than failing the request —
    a transient hiccup on one step's existence probe degrades to "no link" for that step, not a
    500 for the whole editor view."""
    try:
        return store.exists(rel)
    except OSError:
        return False


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


def respond_human(state: ServeState, job_id: str, body: dict[str, Any]) -> tuple[Any, int]:
    """Deliver a human's handoff response to a paused `record` job, resuming it (BE-0179).

    The response is written to the job's stdin as the transport-neutral JSON the record loop reads
    (the same contract the terminal uses). `resumed` is False when the job has no live stdin — it
    already finished or was never handoff-capable.
    """
    job = state.jobs.get(job_id)
    if job is None:
        return {"error": "no such job"}, 404
    response = handoff.HandoffResponse.from_dict(body)
    if state.hosted and response.acted and not response.values:
        # BE-0185 box 3: a takeover asks the human to operate the device directly, but a hosted
        # deployment's author (the multi-tenant `server` backend, BE-0015) is not in front of the
        # worker's device. Refuse rather than pretend — the browser cannot drive the device, and this
        # keeps device reach a first-class precondition instead of assuming it away. The fallback:
        # re-record where the device is, or wire the test-build bypass so `run` needs no live takeover.
        # A value handoff and a cancel still work. `state.hosted` is the only certain "device is not in
        # the author's reach" signal we have: it is set solely by the server backend. A self-hosted
        # local serve reachable over a network (BE-0016) does not set it — detecting that reliably (a
        # loopback bind is not a sound proxy: it false-negatives on SSH forwards and false-positives on
        # a wildcard bind with the author present) is a follow-up; there, the docs point the author at
        # the same fallback.
        return {
            "error": (
                "device takeover is not available on a remote serve — the device is not within "
                "your reach here. Re-record where the device is, or wire the test-build bypass so "
                "the step runs deterministically without a live takeover."
            ),
            "resumed": False,
        }, 409
    resumed = jobs.send_response(job, handoff.response_to_json(response))
    return {"resumed": resumed}, 200


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

    try:
        raw_elements = state.for_org(_org).artifacts.open_bytes(f"{run_id}/{step_id}/elements.json")
    except OSError:
        return {"error": "elements.json is corrupt or unreadable"}, 400
    if raw_elements is None:
        return {"error": "elements.json not found for this step"}, 404

    try:
        raw = json.loads(raw_elements)
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

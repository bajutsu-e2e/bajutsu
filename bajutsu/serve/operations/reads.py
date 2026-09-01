"""Scenario / run-artifact read serve operations (BE-0127)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import yaml

from bajutsu import device_os, handoff
from bajutsu.analysis import stats as _stats
from bajutsu.analytics import ledger as _usage_ledger
from bajutsu.analytics import stats as _usage_stats
from bajutsu.common.evidence import StepView, step_view
from bajutsu.config import Config, load_config, resolve
from bajutsu.drivers import base as driver_base
from bajutsu.scenario import declared_name, load_scenario_file
from bajutsu.scenario.models import STEP_ACTIONS, Scenario, Step
from bajutsu.serve import flakiness as _flakiness
from bajutsu.serve import jobs
from bajutsu.serve.artifacts import Artifact, ArtifactStore
from bajutsu.serve.authz import _record_audit, _target_forbidden
from bajutsu.serve.helpers import (
    list_fs,
    list_simulators,
    list_targets,
    load_serve_config_file,
    valid_run_id,
    valid_scenario_ref,
)
from bajutsu.serve.operations._common import _resolve_org_or_forbid
from bajutsu.serve.operations.config import FS_DISABLED_ERROR, launch_label
from bajutsu.serve.operations.runs import sweep_expired_trash
from bajutsu.serve.server.db import RunRecord
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
    config = parsed[0]
    # Org scoping applies only on a server backend with a system of record; local serve / token mode
    # ignores `orgs:` and lists every target (BE-0015 multi-tenancy).
    if state.repository is None:
        names = list_targets(state.config)
    else:
        names = sorted(state.targets_for(state.org_of(actor)))
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


# The newest-N run window the DB-backed history list, the `/stats` aggregation (BE-0102), and the
# per-target roll-up behind the comparison (BE-0226) all read. One window for those three, because
# a Stats row and the history its drilldown opens (BE-0241) must agree: aggregated over a wider
# window than the list shows, a row names runs the filtered list silently drops, so the detail view
# contradicts the row that opened it (#1718). Two history-wide reads stay outside it deliberately:
# the no-repository history list below is unbounded (a local store has no page to fetch, so its
# list is a superset and a drilldown still resolves), and the Flaky panel reads
# `flakiness.DEFAULT_RUN_LIMIT` — move this window and that one has to move with it, or Flaky
# starts disagreeing with History the way Stats used to. Also the post-filter cap for a
# scenario-scoped DB list, so a scoped picker on the server backend stays as bounded as the
# unscoped one, and the bound on how many `manifest.json` a `/stats` refresh reads out of object
# storage. Large enough to read a trend, not the whole log.
RUN_WINDOW = 200


def _target_scenario_names(state: ServeState, org: str, target: str) -> set[str]:
    """Every scenario name declared in *target*'s suite, as the run picker's scoping key.

    An empty set when the target is another org's (non-leaky, like `list_scenarios`) or has no
    scenarios dir — the caller then offers no run, rather than falling back to the whole history.
    """
    if _target_forbidden(state, org, target):
        return set()
    scope = state.for_org(org).scenarios.scope(target)
    return {n for f in (scope.list() if scope else []) for n in f.get("names") or []}


# The label filter's "every label" choice, so a reader can restore the unfiltered history the
# default (the bound config's own label) narrows away (BE-0404 unit 4).
ALL_LABELS = "*"


def effective_label(state: ServeState, requested: str | None) -> str | None:
    """The label partition to read, or None for the whole history (BE-0404 unit 4).

    Defaults to the bound configuration's own label, which is what makes restarting `serve` against
    a second configuration yield two readable histories rather than one interleaved list.
    `ALL_LABELS` — and a deployment with nothing bound — asks for everything.
    """
    if requested == ALL_LABELS:
        return None
    label = requested or (
        launch_label(state.config, state.config_provenance) if state.config else ""
    )
    return label or None


def apply_label_filter(
    state: ServeState, runs: list[dict[str, Any]], requested: str | None
) -> list[dict[str, Any]]:
    """Narrow *runs* to `effective_label`'s partition — the in-Python half of the filter.

    The database path pushes the same predicate into the query instead (`Repository.list_runs`);
    this serves the artifact-store path, which has no query to push into. An unlabeled run matches
    every label, since it belongs to no partition. An empty match still opens the whole history
    rather than an empty page, so a reader is never left staring at nothing with no filter visible
    to clear.
    """
    label = effective_label(state, requested)
    if label is None:
        return runs
    matching = [r for r in runs if r.get("label") in (label, "", None)]
    return matching or runs


def runs_payload(
    state: ServeState,
    *,
    actor: str | None = None,
    scenario: str | None = None,
    target: str | None = None,
    label: str | None = None,
    ran_target: str | None = None,
) -> tuple[Any, int]:
    """The run history for the actor's org, newest first.

    Two different target filters, deliberately named apart. *target* scopes to the runs whose
    scenarios belong to that target's suite — the Coverage picker's question, answered from the
    scenario names in each run's summary. *ran_target* scopes to the runs the target actually ran,
    read from the `runs.target` stamp (BE-0404 unit 3), which is what the cross-target comparison's
    drill-down needs so its list cannot contradict the row that opened it.
    """
    # Opportunistically purge trash past the retention window before listing (BE-0239) — the lazy
    # sweep, on the history read rather than a background daemon (SqlSessionStore's expiry-on-read
    # precedent). A no-op when retention is disabled; scoped to the actor's org.
    sweep_expired_trash(state, actor=actor)
    # With a system of record (server backend), the history is the actor's org's recorded runs —
    # durable and org-scoped (BE-0015 7c-4). The stored summary mirrors the artifact entry, so the
    # UI shape is identical. Without one (local / stdlib serve), list straight from the artifact
    # store.
    #
    # The scenario and target filters below are *post*-filters — the names they match live in the
    # JSON summary, not an indexed column — so on the DB path the cap must count filtered runs, not
    # global ones: capping first silently drops a matching run that falls outside the newest-N
    # global window and the picker can't reach it (BE-0262 follow-up). The label partition and
    # *ran_target* are not among them: `runs.label` and `runs.target` are ordinary columns, so both
    # push into the query and the window stays on — which keeps the most-hit read of all (the
    # default history list) bounded, and keeps the comparison's drill-down reading the same
    # newest-N window of one target the ranking row beside it was computed over.
    org = state.org_of(actor)
    scoped = scenario is not None or target is not None
    if state.repository is not None:
        partition = effective_label(state, label)
        limit = None if scoped else RUN_WINDOW
        runs = [
            r.summary
            for r in state.repository.list_runs(
                org_id=org, label=partition, target=ran_target, limit=limit
            )
        ]
        if not runs and partition is not None:
            # The bound label matches no run at all — open the whole history rather than an empty
            # page, the same fallback the artifact-store path applies.
            runs = [
                r.summary
                for r in state.repository.list_runs(org_id=org, target=ran_target, limit=limit)
            ]
    else:
        runs = apply_label_filter(state, state.artifacts.list_runs(), label)
        if ran_target is not None:
            # The artifact-store listing carries the manifest's own `target` stamp, so the same
            # partition is a post-filter here — the local stand-in, as the label filter is.
            runs = [r for r in runs if r.get("target") == ran_target]
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
    # Scope the Coverage run picker to the selected target (BE-0146 follow-up): the coverage map is
    # built from *that* target's suite, so a run of another target's scenarios contributes evidence
    # the map cannot place. Scenario name is the same compatibility key the scoping just above uses —
    # a run summary records the names it ran, and no target field — so the two filters compose. A
    # data-driven scenario is recorded under its per-row names, which `declared_name` maps back to
    # the one the suite declares.
    if target is not None:
        names = _target_scenario_names(state, org, target)
        runs = [
            r
            for r in runs
            if any(
                n in names or declared_name(n) in names
                for n in (r.get("scenarios") or [])
                if isinstance(n, str)
            )
        ]
    if scoped and state.repository is not None:
        runs = runs[:RUN_WINDOW]
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


def stats_html(
    state: ServeState, *, actor: str | None = None, label: str | None = None
) -> tuple[str, int]:
    """The aggregate run-stats dashboard (BE-0102) as a self-contained HTML page, org-scoped.

    Reuses the deterministic aggregator over the actor's org run history: read-only, no verdict, no
    LLM. The run-id list comes from the same seam as `runs_payload` (the system of record when wired,
    else the artifact store); each run's full `manifest.json` is read from the artifact store either
    way, since the DB `summary` carries only the compact history-list shape. *label* narrows the
    history to one partition, defaulting to the bound config's own (BE-0404 unit 4).
    """
    # live=True: this is the serve /stats view, so the day/backend/hotspot cells render as drilldown
    # deep links into the SPA's run history (BE-0241); the CLI --html export leaves them plain text.
    return (
        _stats.render_html(_stats.aggregate_runs(_run_manifests(state, actor, label)), live=True),
        200,
    )


def flakiness_html(
    state: ServeState, *, actor: str | None = None, label: str | None = None
) -> tuple[str, int]:
    """The ranked flaky-scenario panel (BE-0220, Half 1) as a self-contained HTML page, org-scoped.

    Ranks the actor's org run history by how much each scenario's verdict flips at a constant
    content fingerprint. When a repository is wired the records come straight from it — the
    provenance stamp the BE-0220 prerequisite added to the run row is the grouping key, so no
    manifest re-read is needed; without one (local / stdlib serve) the same records are built from
    each run's `manifest.json`. Read-only and AI-free: it displays the ranking, deciding no verdict.
    *label* narrows the history to one partition, defaulting to the bound config's own (BE-0404
    unit 4): a flakiness score computed across two configs' interleaved histories is the same defect
    the label exists to fix, so this reads the same partition the run list and `/stats` do.
    """
    return _flakiness.render_html(_flakiness_report(state, actor, label)), 200


def _flakiness_report(
    state: ServeState, actor: str | None, label: str | None = None
) -> _flakiness.FlakinessReport:
    """Rank the actor's org run history — from the DB provenance stamp when wired, else manifests."""
    org = state.org_of(actor)
    if state.repository is not None:
        partition = effective_label(state, label)
        records = state.repository.list_runs(
            org_id=org, label=partition, limit=_flakiness.DEFAULT_RUN_LIMIT
        )
        if not records and partition is not None:
            records = state.repository.list_runs(org_id=org, limit=_flakiness.DEFAULT_RUN_LIMIT)
        _fill_device_runtime(state, org, records)
    else:
        records = _flakiness.records_from_manifests(_run_manifests(state, actor, label))
    return _flakiness.rank_flakiness(records)


def _fill_device_runtime(state: ServeState, org: str, records: list[RunRecord]) -> None:
    """Fill `device_runtime` on runs recorded before the column existed, from their manifests (BE-0358).

    Without it a deployment's history splits at the deploy boundary — older runs under the unknown OS,
    newer ones per OS — so a genuine flake spanning that boundary reads as two `unproven` histories:
    BE-0358's own misclassification with the sign reversed. No migration can repair those rows, since
    the per-scenario label lives in the run's `manifest.json` in the artifact store, not in the
    database.

    **Repaired for this request only, never written back.** Persisting would put a write on a read
    path, where `record_run`'s full-row upsert would insert a row it does not find — resurrecting a
    run an operator had hard-purged between the listing and this loop, silently and with no audit
    trail. The cost of re-reading is bounded and self-limiting instead: every run recorded since the
    column exists carries a determined value (a label, or `""` for a run that named no single OS), so
    only pre-column rows are ever read, and they age out of the newest-N window. A row is also skipped
    when it has no `scenario_hash`, since `rank_flakiness` cannot group it whatever its OS turns out
    to be.

    A run whose manifest is gone stays undetermined and keeps grouping under the unknown OS, which the
    report discloses rather than passing off as evidence.
    """
    pending = [r for r in records if r.device_runtime is None and isinstance(r.scenario_hash, str)]
    artifacts = state.for_org(org).artifacts
    for record in pending:
        # Read one id at a time: `run_set_manifests` skips a manifest it can't parse, so a returned
        # list can't be zipped back onto the ids, and keying on each manifest's self-reported `runId`
        # would attribute a copied or restored run's OS to whichever row shares that id.
        manifests = run_set_manifests(artifacts, [record.id])
        if not manifests:
            continue
        run_os = device_os.from_manifest(manifests[0])
        record.device_runtime = run_os.label if run_os is not None else ""


def _run_manifests(
    state: ServeState, actor: str | None, label: str | None = None
) -> list[dict[str, Any]]:
    """The newest runs' parsed `manifest.json` for the actor's org; unreadable/malformed ones skipped.

    The ids come from the recorded runs when a repository is wired (org-scoped), else the artifact
    store's own listing; both are newest-first and bounded to the same `RUN_WINDOW` so a
    `/stats` refresh over a large history stays cheap and the two backends aggregate the same set. The
    manifests are always read from the org's artifact store — the seam that holds the full manifest
    whether or not a database indexes the runs — keyed by the canonical run id.
    """
    org = state.org_of(actor)
    artifacts = state.for_org(org).artifacts
    rows: list[dict[str, Any]]
    if state.repository is not None:
        partition = effective_label(state, label)
        rows = [
            {"id": r.id}
            for r in state.repository.list_runs(org_id=org, label=partition, limit=RUN_WINDOW)
        ]
        if not rows and partition is not None:
            rows = [{"id": r.id} for r in state.repository.list_runs(org_id=org, limit=RUN_WINDOW)]
    else:
        rows = apply_label_filter(state, artifacts.list_runs(), label)[:RUN_WINDOW]
    return run_set_manifests(artifacts, [r.get("id") for r in rows])


def run_set_manifests(store: ArtifactStore, run_ids: Iterable[Any]) -> list[dict[str, Any]]:
    """Read the parsed `manifest.json` of each run in *run_ids* from *store*, skipping bad ones.

    The `ServeState`/actor-free core of `_run_manifests`: it takes the run set explicitly, so the
    same aggregation can be run once per target over a target-scoped run set (BE-0226) rather than
    only over the active config's org run history. An id that is not a single safe segment is
    rejected before it becomes a path (serve's containment model, BE-0015), and an unreadable or
    malformed manifest is skipped — the aggregator never fails on one bad run. Each manifest already
    carries its own `runId` (`bajutsu.common.report.manifest.manifest_dict`), so a caller that needs to
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

    Reads the same attributed ledgers the serve process's AI subprocesses append to and aggregates
    them deterministically: read-only, no verdict, no LLM. A disabled or absent ledger is not an
    error — it aggregates to the empty state, which explains how recording is enabled (graceful
    degradation, like the readiness panels). A ledger file is shared by every writer that resolves to
    it, so the aggregate is not an org's own usage and *actor* narrows only which ledgers are read,
    never which events within one count (org-scoping the ledger itself is a design change, tracked
    apart from issue #1717).
    """
    events: list[_usage_ledger.UsageEvent] = []
    for path in _usage_ledger_paths(state, actor):
        try:
            events.extend(_usage_ledger.read_events(path))
        except OSError:
            # An unreadable ledger (a permission issue, a transient I/O error) degrades to skipping
            # that file rather than a 500 — the same "skip what can't be read" promise `/stats` makes.
            continue
    return _usage_stats.render_html(_usage_stats.aggregate_usage(events)), 200


def _usage_ledger_paths(state: ServeState, actor: str | None) -> list[Path]:
    """Every ledger file the dashboard reads — resolved as the AI subprocesses serve spawns do.

    AI work in serve runs as subprocesses that call `usage_ledger.configure_from_ai_config` with the
    *target-merged* `ai` block (`resolve`'s `Effective.ai`, so `targets.<name>.ai.usageLedger`
    overrides `defaults.ai.usageLedger`), writing relative paths against their cwd — `state.cwd`,
    the directory `jobs` spawns them in absent a per-job override. Resolving the read side any other
    way is what left the dashboard reading an empty file while a per-target ledger filled up (issue
    #1717).

    Every such subprocess names a target (`resolve` rejects an unknown one), so the set of files it
    can append to is the union over the targets — the dashboard is process-wide, not target-scoped,
    so it reads all of them. Which targets those are is the same question `list_targets_payload` and
    `read_scenario` answer: on a server backend the actor's org owns a subset of them (BE-0015 /
    BE-0375), and walking every entry would merge another org's ledger into this view, so the walk
    goes through `targets_for`. Local serve / token mode ignores `orgs:` and walks them all, exactly
    as the target list does. A config that declares no targets can have no such writer; the dashboard
    falls back to `defaults.ai` — the config's only statement of where a ledger would live, and what
    a writer would inherit once a target exists — rather than reading nothing at all. An explicit
    empty `usageLedger` disables persistence, contributing no path.

    Two writers stay outside that union by construction, both pre-existing and neither this
    dashboard's to reach: a job carrying its own `cwd` (a remote worker's workspace, which serve
    cannot read at all), and a targetless `bajutsu triage --ai` launched beside serve, which resolves
    no `ai` block of its own and so writes the built-in default rather than the configured ledger.
    """
    loaded = load_serve_config_file(state.config)  # cached parse; None when absent/unreadable
    if loaded is None:
        return _absolute_ledger_paths(state, [None])
    config = loaded[0]
    if not config.targets:
        defaults = config.defaults.ai
        ledger = defaults.usage_ledger if defaults is not None else None
        return _absolute_ledger_paths(state, [ledger])
    # Org scoping applies only on a server backend with a system of record, mirroring
    # `list_targets_payload` — the one place target ownership is decided (BE-0015 multi-tenancy).
    names = (
        sorted(config.targets)
        if state.repository is None
        else state.targets_for(state.org_of(actor))
    )
    configured: list[str | None] = []
    for target in sorted(names):
        try:
            merged = resolve(config, target).ai
        except (ValueError, KeyError):
            continue  # a target that won't resolve can't have run an AI path either
        configured.append(merged.usage_ledger if merged is not None else None)
    return _absolute_ledger_paths(state, configured)


def _absolute_ledger_paths(state: ServeState, configured: Iterable[str | None]) -> list[Path]:
    """The distinct absolute ledger files *configured* names, dropping the disabled ones.

    Deduplication is on the resolved path, not the configured string: two targets naming the same
    file differently (`runs/usage.jsonl` vs `./runs/usage.jsonl`) must be read once, or every event
    in it would count twice.
    """
    paths: dict[Path, None] = {}
    for value in configured:
        path = _usage_ledger.resolve_ledger_path(value)
        if path is None:  # persistence disabled for this target
            continue
        if not path.is_absolute():
            path = state.cwd / path
        try:
            key = path.resolve()
        except OSError:  # a symlink loop or an unreadable parent — dedupe on the unresolved path
            key = path
        paths.setdefault(key, None)
    return list(paths)


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
    if not isinstance(manifest, dict):
        return []

    effective_name = scenario_name or matched.name
    scenario = _find_scenario(manifest, effective_name)
    # `or None` coerces a falsy/empty `sid` (e.g. `""`) to `None` too, so a malformed scenario
    # record bails to `[]` below rather than building a malformed step id like `/step0`. Parenthesized
    # so `scenario.get(...)` never runs when `scenario` is `None` regardless of how this expression
    # is later refactored or read.
    sid = (scenario.get("sid") or None) if scenario is not None else None
    # Not just `str`-narrowed: `_valid_step_id` (already the gate `resolve_scenario_pick` applies to
    # a `stepId` coming *back* from the client) also rejects `..`/absolute segments here, at the
    # point every `stepId` this function returns is built from `sid` — so a malformed manifest can't
    # produce a traversal-shaped id in the first place, not just have one rejected on its way back.
    if not isinstance(sid, str) or not _valid_step_id(sid):
        return []
    # step id (parsed from each outcome's own recorded artifact paths) -> that step's artifacts, so
    # the loop below resolves the real names the run recorded (BE-0341) rather than assuming fixed
    # `before.png`/`after.png`. Keyed by the runtime step id, not the outcome's `index`, which
    # counts nested `if`/`forEach`/`web` steps the loop below does not. Skips a non-`dict` entry
    # rather than raising, so a malformed manifest degrades to missing artifacts, not a 500.
    steps = (scenario or {}).get("steps")
    artifacts_by_step_id: dict[str, list[dict[str, Any]]] = {}
    for out in steps if isinstance(steps, list) else []:
        if not isinstance(out, dict):
            continue
        step_artifacts = out.get("artifacts")
        if not isinstance(step_artifacts, list):
            continue
        # Filtered once, up front, to `dict` entries only: `_artifact_names` below calls `.get` on
        # each one, so a stray non-`dict` artifact must never reach it. The step id then comes from
        # the first artifact that is both a `dict` and has a usable, safe (`_valid_step_id`) `name`
        # — not merely the first `dict` — so one malformed entry ahead of a valid one never stops
        # the search. `_valid_step_id`, not a bare `"/" in name` check: a traversal-shaped name
        # (e.g. `../../../run2/...`) would otherwise become the key itself, hiding every other,
        # legitimate artifact recorded for this same step under a key no real `step_id` ever
        # matches (BE-0341).
        dict_artifacts = [a for a in step_artifacts if isinstance(a, dict)]
        name = next(
            (
                a["name"]
                for a in dict_artifacts
                if isinstance(a.get("name"), str) and "/" in a["name"] and _valid_step_id(a["name"])
            ),
            None,
        )
        if name is not None:
            artifacts_by_step_id[name.rsplit("/", 1)[0]] = dict_artifacts

    result: list[dict[str, Any]] = []
    for idx, step in enumerate(matched.steps):
        step_id = f"{sid}/{step.name or f'step{idx}'}"
        action, fields = _step_action_fields(step)
        view = _artifact_names(
            artifacts_by_step_id.get(step_id, []),
            lambda name: _safe_exists(artifacts, f"{run_id}/{name}"),
        )
        # An unpaired step offers no image at all. The picker's whole job is to turn a click on
        # these pixels into a selector resolved through this tree, so an image the tree does not
        # describe would resolve every click to the wrong element — silently, since the two look
        # like an ordinary pair. `step_view` reports unpaired only when an image is actually there,
        # so this stays distinct from a step that has no screenshot to offer, which the editor
        # words differently.
        unpaired = not view.paired
        result.append(
            {
                "stepId": step_id,
                "action": action,
                "fields": fields,
                "elementsUrl": f"/runs/{run_id}/{view.elements}"
                if view.elements is not None
                and _safe_exists(artifacts, f"{run_id}/{view.elements}")
                else None,
                # No existence check here: `step_view` chose among the names the store actually
                # holds, so a name at all means the file is there.
                "screenshotUrl": f"/runs/{run_id}/{view.screenshot}"
                if view.screenshot is not None and view.paired
                else None,
                "screenshotUnpaired": unpaired,
            }
        )
    return result


def _find_scenario(manifest: dict[str, Any], scenario_name: str | None) -> dict[str, Any] | None:
    """The manifest's own scenario record for *scenario_name* (its `sid` and per-step outcomes)."""
    scenarios = manifest.get("scenarios", [])
    if not isinstance(scenarios, list):
        return None
    for scn in scenarios:
        if isinstance(scn, dict) and scn.get("scenario") == scenario_name:
            return scn
    return None


def _artifact_names(
    step_artifacts: list[dict[str, Any]], exists: Callable[[str], bool]
) -> StepView:
    """The `elements` / `screenshot` artifacts the editor shows for a step, or `None` for either
    the run never recorded (BE-0341), plus whether the two describe the same screen.

    Resolved by `step_view`, so the editor's element picker, the HTML report, and the triage context
    resolve one step to one image: the post-action `after.png` when the run recorded one, else the
    pre-step baseline's `before.png`. Both consumers share that one image because only one tree
    survives the step: the post-step `elements` write replaces the baseline's pre-action tree, so no
    pre-action pair is left for the picker to resolve against. The picker writes its resolved
    selector back into the same step, so a step that navigates offers the screen it reached rather
    than the one it targets — `docs/web-ui.md` (Author → Edit) sends an author to a live session for
    that case.

    A `StepView` reporting `paired=False` is why the caller withholds the image: the picker resolves
    a click on those pixels through this tree, so an image the tree does not describe would author a
    selector for an element that was never where the author clicked.

    Args:
        step_artifacts: the step's manifest entries, already narrowed to `dict`s by the caller — a
            stray non-`dict` never reaches here, so the `.get` calls below need no guard of their
            own.
        exists: whether the store actually holds a named artifact. The manifest can name a file the
            store no longer holds (a run restored from Trash, or one synced into an object store
            that never received the last write), and choosing `after.png` from the names alone
            would leave the step with no image to pick against while its `before.png` sits right
            there. `step_view` probes lazily, in preference order: this call site's `exists` is a
            live object-store lookup on the hosted backend, and `read_scenario` walks every step of
            a scenario, so filtering all candidates up front would cost one round trip per recorded
            screenshot per step — two, now that every acting step records both — for a fallback that
            fires almost never.
    """
    entries: list[tuple[str, str, str | None]] = []
    for art in step_artifacts:
        kind, name, depicts = art.get("kind"), art.get("name"), art.get("depicts")
        # Narrowed to `str`, not just non-`None`: a malformed/partially written manifest could carry
        # a non-string value here, which would otherwise flow into the URL/path built from it below.
        # `_valid_step_id` (a generic safe-relative-path check despite its name) also rejects an
        # absolute or `..`-shaped `name`: `LocalArtifactStore` only enforces containment to
        # `runs_dir`, not per-run, so a traversal-shaped name could otherwise resolve to an
        # unrelated artifact elsewhere in the org's runs tree.
        if isinstance(kind, str) and isinstance(name, str) and _valid_step_id(name):
            entries.append((kind, name, depicts if isinstance(depicts, str) else None))
    return step_view(entries, exists=exists)


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
    """Save an edited scenario back to its ``*.yaml`` (bounded to the target's scenarios dir).

    The response's ``overwritten`` flag is accurate wherever a scope's ``read`` and ``save`` share
    one backing store (the local filesystem scope, and a hosted scope backed purely by object
    storage). It is **not** storage-accurate for a hosted config sourced from a Git/local-tree
    checkout (`LocalTreeScenarioStorage`): BE-0324 deliberately splits that scope's `read` (the
    extracted local tree) from its `save` (a separate object store), so the pre-write probe below
    checks a different store than the one the write actually lands on, and can report either
    direction wrong. Reporting a storage-accurate flag there needs the writer itself to report
    whether it replaced something — a `ScenarioScope.save` contract change bigger than this
    operation, tracked as a follow-up rather than attempted here."""
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
    try:
        # A file that exists but can't be decoded (bad encoding, a permission error mid-read)
        # still proves *something* is there to overwrite — degrade to "overwritten" rather than
        # letting the probe itself 500 a save that would otherwise succeed (mirrors
        # LocalTreeScenarioStorage.read's own leniency, bajutsu/serve/server/scenarios.py).
        overwritten = scope.read(ref) is not None
    except (OSError, ValueError):
        overwritten = True
    saved = scope.save(ref, text)
    if saved is None:
        return {"error": "path must be a *.yaml under the scenarios dir"}, 400
    _record_audit(
        state,
        actor,
        org,
        "scenario.save",
        target or "",
        {"path": ref, "overwritten": overwritten},
    )
    return {"ok": True, "path": saved, "overwritten": overwritten}, 200


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

    One response never reaches stdin: a device-operation takeover (`acted`, no values) on a hosted
    serve is refused with a 409 and `resumed=False` (BE-0185 box 3) — the author is not in front of
    the worker's device there, so the browser cannot drive it. Value handoffs and cancels still pass
    through.
    """
    job = state.jobs.get(job_id)
    if job is None:
        return {"error": "no such job"}, 404
    response = handoff.HandoffResponse.from_dict(body)
    if state.hosted and response.kind == "acted" and response.acted:
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


# Each return is a distinct HTTP status from a validation guard — the early-return shape RET505
# itself asks for (BE-0386).
def resolve_scenario_pick(  # noqa: PLR0911
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
                # Carried through rather than blanked: this rebuilds an `Element` from what the file
                # actually recorded, and a reconstruction that drops a field the file holds would
                # claim an absence the evidence contradicts. Nothing downstream reads it today.
                "nativeZ": driver_base.native_z_from_json(el.get("nativeZ")),
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

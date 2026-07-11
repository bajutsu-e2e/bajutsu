"""Project-hub serve operations (BE-0225 unit 3): the five `/api/projects…` endpoints.

Additive to the single-config endpoints — a project is a named binding to a config source, and this
module lists / registers / deregisters them, lists a project's runs, and triggers a run for the
active project. Every function is org-scoped (resolving to the single `default` org locally) and sits
behind the `ProjectRegistry` seam, so the same logic drives the DB-backed and the on-disk-JSON
registries alike. All logic is deterministic — a project is a config binding and a run is the same
`bajutsu run` the launcher spawns; no LLM enters the path.
"""

from __future__ import annotations

from typing import Any

from bajutsu.serve.operations.config import config_sources
from bajutsu.serve.operations.dispatch import start_run
from bajutsu.serve.state import ServeState

# The registry stores a config-source `kind` (`git` / `upload` / `file`); the UI/allowlist names the
# same three as `git` / `upload` / `fs` (`config_sources`, BE-0108). Map so a stored kind is checked
# against what the deployment offers — hosted refuses `file`, local allows all three.
_KIND_TO_SOURCE = {"git": "git", "upload": "upload", "file": "fs"}

_NO_HUB = ({"error": "the project hub is not configured"}, 400)


def _validate_source(state: ServeState, source: Any) -> tuple[Any, int] | None:
    """Screen a config-source record against the deployment's allowlist (BE-0108), or None if fine.

    A ``None`` source is an unbound/rename-only registration and always passes. Otherwise the record
    must be an object naming a known `kind` that this deployment offers — so a hosted server refuses a
    client-supplied filesystem path exactly as `bind_config` does, not merely hiding it in the UI.
    """
    if source is None:
        return None
    if not isinstance(source, dict):
        return {"error": "source must be an object"}, 400
    kind = source.get("kind")
    mapped = _KIND_TO_SOURCE.get(kind) if isinstance(kind, str) else None
    if mapped is None:
        return {"error": f"unknown config source kind: {kind!r}"}, 400
    if mapped not in config_sources(state):
        return {"error": f"config source {kind!r} is not allowed on this server"}, 403
    return None


def _project_view(name: str, source: Any, *, active: bool) -> dict[str, Any]:
    return {"name": name, "source": source, "active": active}


def list_projects_view(state: ServeState, *, actor: str | None = None) -> tuple[Any, int]:
    """The org's registered projects — each with its config source, whether it is active, and its
    latest run summary — for the switcher and projects list (unit 4 renders this)."""
    registry = state.project_registry
    if registry is None:
        return [], 200
    org = state.org_of(actor)
    active = registry.resolve_active(org_id=org)
    active_id = active.id if active is not None else None
    projects = registry.list_projects(org_id=org)
    latest = _latest_run_summaries(state, org, projects)
    return [
        {
            **_project_view(p.name, p.source, active=p.id == active_id),
            "lastRun": latest.get(p.id),
        }
        for p in projects
    ], 200


def register_project(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Register a project from a config source, or rebind an existing one by name (BE-0108-screened).

    Idempotent by name (the `add` seam reuses an existing id), so an explicit name disambiguates two
    configs from the same Git repo that unit 2's repo-only auto-name would fold together. The first
    project registered in an org with no active project becomes active, so a non-`default` org gains
    an active project through the API — unit 2's boot auto-registration only covered the launch config.
    """
    registry = state.project_registry
    if registry is None:
        return _NO_HUB
    name = str(body.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    if "/" in name:
        return {"error": "name must not contain '/'"}, 400
    source = body.get("source")
    invalid = _validate_source(state, source)
    if invalid is not None:
        return invalid
    org = state.org_of(actor)
    had_active = registry.resolve_active(org_id=org) is not None
    project = registry.add(org_id=org, name=name, source=source)
    if not had_active:
        registry.set_active(org_id=org, name=name)
    active = registry.resolve_active(org_id=org)
    return _project_view(
        project.name, project.source, active=active is not None and active.id == project.id
    ), 200


def deregister_project(
    state: ServeState, name: str, *, actor: str | None = None
) -> tuple[Any, int]:
    """Deregister a project by name; its runs are retained on disk, only the binding is removed."""
    registry = state.project_registry
    if registry is None:
        return _NO_HUB
    org = state.org_of(actor)
    if registry.get(org_id=org, name=name) is None:
        return {"error": f"no project named {name!r}"}, 404
    registry.remove(org_id=org, name=name)
    return {"ok": True}, 200


def run_project(
    state: ServeState, name: str, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Enqueue a run for a project — the external-trigger target CI/cron addresses by name.

    The run goes through the same `start_run` path (same body: scenario + target), so it is the same
    deterministic `bajutsu run` and the active project's id is stamped onto the produced run. Running a
    project other than the active binding needs the live rebind unit 4's switcher owns, so this refuses
    it (409) rather than run the wrong config.
    """
    registry = state.project_registry
    if registry is None:
        return _NO_HUB
    org = state.org_of(actor)
    project = registry.get(org_id=org, name=name)
    if project is None:
        return {"error": f"no project named {name!r}"}, 404
    active = registry.resolve_active(org_id=org)
    if active is None or active.id != project.id:
        return {"error": f"project {name!r} is not the active binding; switch to it first"}, 409
    return start_run(state, body, actor=actor)


def project_runs(state: ServeState, name: str, *, actor: str | None = None) -> tuple[Any, int]:
    """A project's run history (newest first) — the per-project slice the cross-project dashboard
    (BE-0226) aggregates. The `runs.project_id` column with a database, the project→run-ids index
    without one."""
    registry = state.project_registry
    if registry is None:
        return _NO_HUB
    org = state.org_of(actor)
    project = registry.get(org_id=org, name=name)
    if project is None:
        return {"error": f"no project named {name!r}"}, 404
    if state.repository is not None:
        return [
            r.summary for r in state.repository.list_runs(org_id=org, project_id=project.id)
        ], 200
    # Local: the run-ids index tags which runs belong to the project; the summaries come from the same
    # artifact-store listing `runs_payload` uses, filtered to those ids and kept in its newest-first
    # order so the two views agree on shape and ordering.
    tagged = set(registry.run_ids(org_id=org, project_id=project.id))
    listing = state.for_org(org).artifacts.list_runs()
    return [r for r in listing if r.get("id") in tagged], 200


def _latest_run_summaries(
    state: ServeState, org: str, projects: list[Any]
) -> dict[str, dict[str, Any]]:
    """Each project's newest run summary, keyed by project id, for `list_projects_view`.

    Accepts the pre-fetched project list from the caller so `list_projects` is not called a second
    time — one query for the list, then one per project (DB) or one store scan (local).
    """
    registry = state.project_registry
    if registry is None:
        return {}
    if state.repository is not None:
        # The DB partitions by the column; ask for each project's newest run only.
        out: dict[str, dict[str, Any]] = {}
        for p in projects:
            recent = state.repository.list_runs(org_id=org, project_id=p.id, limit=1)
            if recent:
                out[p.id] = recent[0].summary
        return out
    by_id = {r.get("id"): r for r in state.for_org(org).artifacts.list_runs()}
    latest: dict[str, dict[str, Any]] = {}
    for p in projects:
        ids = registry.run_ids(org_id=org, project_id=p.id)  # newest first
        summary = next((by_id[rid] for rid in ids if rid in by_id), None)
        if summary is not None:
            latest[p.id] = summary
    return latest

"""Run-lifecycle serve operations: soft-delete / restore / purge a run and its report (BE-0239).

A pure serve-mode lifecycle over already-recorded data — no LLM and no `run`/CI verdict anywhere on
this path (prime directive 1), so it is deterministic by construction. Soft-delete parks a run in
the trash so it drops out of the history lists but stays restorable for the retention window; purge
(admin) is the one irreversible step. Every action is org-scoped (a run in another org's prefix is a
not-found, so BE-0015 multi-tenancy holds for delete too), audit-logged, and emits a structured
`oplog` event so an SRE can grep an irreversible purge (BE-0055).

Both stores diverge in implementation but not contract: the local filesystem store moves a run under
``runs/.trash/``; the object store writes a ``<run_id>/.deleted`` tombstone. On the hosted backend a
run also has a DB row, whose `deleted_at` column drives the DB-backed `list_runs`, so a soft-delete
updates *both* the store (which the object-store crawl listing reads) and the repository.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from bajutsu.serve import oplog
from bajutsu.serve.authz import _record_audit, role_allows
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.state import ServeState

_logger = logging.getLogger("bajutsu.serve.operations")

_NOT_FOUND = ({"error": "no such run"}, 404)


def _forbidden_purge(state: ServeState, actor: str | None) -> bool:
    """Whether *actor* may not purge — the admin gate the path-based RBAC can't apply (`?purge=true`
    isn't in the path `required_role` sees). Mirrors `forbidden_for_role`: local (no repository) and
    a token/operator request (no identity) are full-access; an OAuth user needs the admin role."""
    if state.repository is None or actor is None:
        return False
    role = state.repository.user_role(actor) or "viewer"
    return not role_allows(role, "admin")


def _apply_delete(
    state: ServeState, org: str, run_id: str, *, purge: bool, actor: str | None, now: datetime
) -> bool:
    """Soft-delete or purge one run in *org*, recording audit + oplog when it acts. Returns whether
    anything was removed — False for a bad id or a run absent from both the store and the DB, which
    the caller maps to a 404. Store and repository are updated together so the object-store crawl
    listing (tombstone) and the DB-backed run listing (`deleted_at`) stay in step."""
    store = state.for_org(org).artifacts
    repo = state.repository
    if purge:
        acted = store.purge_run(run_id)
        if repo is not None:
            acted = repo.purge_run(run_id, org_id=org) or acted
        action, event, msg = "run.purge", "run.purged", "run purged"
    else:
        acted = store.soft_delete_run(run_id)
        if repo is not None:
            acted = repo.soft_delete_run(run_id, org_id=org, deleted_by=actor, at=now) or acted
        action, event, msg = "run.soft_delete", "run.soft_deleted", "run soft-deleted"
    if acted:
        _record_audit(state, actor, org, action, run_id, {})
        oplog.log_event(_logger, event, msg, run_id=run_id, org=org, actor=actor)
    return acted


def delete_run(
    state: ServeState, run_id: str, *, purge: bool = False, actor: str | None = None
) -> tuple[Any, int]:
    """Soft-delete *run_id* (the trash window), or purge it immediately when *purge* (admin-only).

    404 for a bad or absent id; 403 when a non-admin asks to purge. The soft path is reversible via
    `restore_run` within the retention window; purge is not."""
    if not valid_run_id(run_id):
        return _NOT_FOUND
    if purge and _forbidden_purge(state, actor):
        return {"error": "forbidden"}, 403
    org = state.org_of(actor)
    if not _apply_delete(state, org, run_id, purge=purge, actor=actor, now=datetime.now(UTC)):
        return _NOT_FOUND
    return {"ok": True, "purged": purge}, 200


def restore_run(state: ServeState, run_id: str, *, actor: str | None = None) -> tuple[Any, int]:
    """Undo a soft-delete for *run_id*, returning it to the history lists (BE-0239). 404 when no
    trashed run holds the id (a bad id, never-deleted, already-purged, or already-restored run)."""
    if not valid_run_id(run_id):
        return _NOT_FOUND
    org = state.org_of(actor)
    store = state.for_org(org).artifacts
    repo = state.repository
    acted = store.restore_run(run_id)
    if repo is not None:
        acted = repo.restore_run(run_id, org_id=org) or acted
    if not acted:
        return _NOT_FOUND
    _record_audit(state, actor, org, "run.restore", run_id, {})
    oplog.log_event(_logger, "run.restored", "run restored", run_id=run_id, org=org, actor=actor)
    return {"ok": True}, 200


def bulk_delete_runs(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Soft-delete (or purge, admin-only) many runs at once — the "一括削除" case (BE-0239).

    Body: ``{"ids": [...], "purge": bool}``. Each id is applied independently; the response lists
    which ids were `deleted` and which were `notFound`, so one bad id doesn't fail the batch."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return {"error": "ids must be a list of strings"}, 400
    # Strict JSON boolean, not a truthy coercion: a client sending the string "false" (e.g. copying
    # the DELETE routes' query-string convention) must NOT trigger an irreversible purge — `bool(
    # "false")` is True, so `is True` is the safe check for a destructive default-off flag.
    purge = body.get("purge") is True
    if purge and _forbidden_purge(state, actor):
        return {"error": "forbidden"}, 403
    org = state.org_of(actor)
    now = datetime.now(UTC)
    deleted: list[str] = []
    not_found: list[str] = []
    for run_id in ids:
        acted = valid_run_id(run_id) and _apply_delete(
            state, org, run_id, purge=purge, actor=actor, now=now
        )
        (deleted if acted else not_found).append(run_id)
    return {"deleted": deleted, "notFound": not_found, "purged": purge}, 200


def sweep_expired_trash(
    state: ServeState, *, actor: str | None = None, now: datetime | None = None
) -> int:
    """Purge soft-deleted runs past the retention window for the actor's org, returning how many.

    A lazy sweep (BE-0239): called opportunistically on a history read rather than by a background
    daemon, matching `SqlSessionStore`'s expiry-on-read (no periodic-job runner exists in serve). A
    no-op when retention is disabled (``run_retention_days <= 0``). *now* is injectable for tests."""
    days = state.run_retention_days
    if days <= 0:
        return 0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    org = state.org_of(actor)
    store = state.for_org(org).artifacts
    repo = state.repository
    # Eligible run ids from both trash records, deduped (dict preserves insertion order): the store's
    # trash-dir/tombstone, plus — on the hosted backend — the DB's `deleted_at`. A run trashed only in
    # the DB (soft-deleted before any evidence upload, so no store tombstone) is invisible to the
    # store scan but still needs auto-purging, and vice versa for a local run with no DB.
    expired: dict[str, None] = {}
    for entry in store.list_trashed_runs():
        deleted_at = _parse_iso(entry.get("deletedAt"))
        if deleted_at is not None and deleted_at <= cutoff:  # skip unknown time / still in-window
            expired[str(entry["id"])] = None
    if repo is not None:
        for rec in repo.list_deleted_runs(org_id=org, before=cutoff):
            expired[rec.id] = None
    for run_id in expired:
        store.purge_run(run_id)
        if repo is not None:
            repo.purge_run(run_id, org_id=org)
        # A retention purge has no human actor; audit/oplog still record which run and why, so the
        # irreversible removal is greppable (BE-0055) — the same as a user-initiated purge.
        _record_audit(state, None, org, "run.purge", run_id, {"reason": "retention"})
        oplog.log_event(_logger, "run.purged", "run purged by retention", run_id=run_id, org=org)
    return len(expired)


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 deletion timestamp, or None if it's missing/unparseable (so a hand-corrupt
    tombstone is skipped by the sweep, never crashing it)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # A naive timestamp (no offset) is read as UTC so the comparison against the tz-aware cutoff is
    # well-defined; the stores always write a tz-aware value, so this only hardens a hand-edited one.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

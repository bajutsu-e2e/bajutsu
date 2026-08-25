"""The `Repository` seam: the hosted backend's system of record (BE-0015 7a).

Shaped like the other server seams (`object_store.py`): a `Protocol`, a SQLAlchemy implementation,
and an env-driven factory — with SQLAlchemy and the ORM models lazy-imported inside the functions
that need them, so the default `serve`/CLI path never loads them (the import guard locks this).
7a ships the `runs` methods (`RunRecord`); BE-0225 extends the same seam with project methods
(`ProjectRecord`: `create_project`, `get_project`, `list_projects`, `delete_project`). ORM rows
never leak past the seam — only the boundary types cross."""

from __future__ import annotations

import math
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from bajutsu.serve.server.models import Org, Project, Run

# A lease with no heartbeat for this long is treated as a dead worker and reclaimed; a job that is
# reclaimed this many times is failed rather than re-queued forever (BE-0016 worker liveness). The
# worker's heartbeat interval must stay well under the timeout so a live long run is never reclaimed.
DEFAULT_LEASE_TIMEOUT_SECONDS = 120.0
DEFAULT_LEASE_MAX_ATTEMPTS = 3

# The default newest-N window `list_runs` caps at (`None` means unbounded). Shared so the serve
# read path that re-caps a scoped list post-filter (`operations.reads`) stays in lock-step: a future
# bump here must not silently leave that path capping at a stale number.
DEFAULT_RUN_LIMIT = 50


@dataclass
class LeasedJob:
    """A job that has been leased by a worker — the boundary type the seam hands out."""

    id: str
    org_id: str
    spec: dict[str, Any]


@dataclass
class JobMetrics:
    """An aggregate read of the jobs table for the ``/metrics`` endpoint (BE-0169).

    Every field is derived from rows the lease path already maintains — this adds no bookkeeping.
    Ages are seconds relative to the server clock at snapshot time; ``leased_at`` doubles as the
    worker's last-heartbeat timestamp (the worker renews it on its heartbeat interval), so its age
    is the liveness signal.
    """

    queued_by_org: dict[str, int]  # org_id -> jobs waiting in the queue
    leased_by_org: dict[str, int]  # org_id -> jobs leased to a worker (in flight)
    # worker_id -> seconds since its freshest lease renewal; rising past the lease timeout = dead
    heartbeat_age_by_worker: dict[str, float]
    # Seconds since the oldest in-flight (leased) job was *enqueued* (created_at), so it includes
    # the time it waited in the queue before the lease — a slow / stuck-run signal; 0.0 if none
    oldest_in_flight_seconds: float
    # Queued jobs no *live* worker can serve — their required capabilities are a subset of no live
    # worker's advertised set (BE-0166). A rising count is the operator's "add a worker with X"
    # signal; such a job stays queued rather than being leased to an incompatible worker or dropped.
    unroutable_queued: int = 0


@dataclass
class ProjectRecord:
    """A registered project as the seam exchanges it — a named config binding within an org.

    `source` is the discriminated config-source record (`{"kind": ..., "locator": ...}`) the project
    binds, or None for a row that predates the binding (BE-0015's unwired `projects` scaffolding).
    """

    id: str
    org_id: str
    name: str
    source: dict[str, Any] | None = None
    created_at: datetime | None = None


@dataclass
class OrgRecord:
    """An org as the seam exchanges it — its identity plus the membership that decides sign-in.

    `members` / `github_orgs` / `github_teams` / `editor_teams` mirror `OrgConfig`'s own fields
    (BE-0375); a row that predates the move, or one `ensure_org` created at sign-in, carries empty
    lists throughout.
    `membership_seeded_at` is the per-row cutover marker (set = the database owns this org's
    membership), `deleted_at` the soft-delete marker.
    """

    id: str
    slug: str
    name: str
    members: list[str] = field(default_factory=list)
    github_orgs: list[str] = field(default_factory=list)
    github_teams: list[str] = field(default_factory=list)
    editor_teams: list[str] = field(default_factory=list)
    membership_seeded_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime | None = None


@dataclass
class RunRecord:
    """A run as the seam exchanges it — the relational core plus the JSON manifest summary."""

    id: str
    org_id: str
    status: str
    project_id: str | None = None
    created_by: str | None = None
    ok: bool | None = None
    created_at: datetime | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    # Run provenance mirrored from the run's manifest.json (BE-0049 stamp); with `device_runtime`
    # below, the grouping key for cross-run flakiness (BE-0220). None for a pre-provenance run.
    scenario_hash: str | None = None
    tool_version: str | None = None
    git_revision: str | None = None
    # The OS label every scenario in the run ran on (`"iOS 18.6"`), the other half of the flakiness
    # grouping key (BE-0358). Three states: the label; `""` when the run was read but named no single
    # OS (no device catalog, or scenarios spanning versions); None when it was never determined — a
    # row recorded before this field existed, which the hosted panel backfills from its manifest.
    device_runtime: str | None = None
    # Soft-delete marker (BE-0239): when set, the run is trashed — hidden from `list_runs` unless
    # `include_deleted`. None for a live run. `record_run` never writes these (a status update must
    # not resurrect or re-trash a run); only `soft_delete_run`/`restore_run` touch them.
    deleted_at: datetime | None = None
    deleted_by: str | None = None


@runtime_checkable
class Repository(Protocol):
    """Persistence for the control plane. 7a covers runs; identity/audit land in 7b/7c."""

    def record_run(self, run: RunRecord) -> None:
        """Insert *run*, or update it in place when its id already exists (e.g. a status change)."""

    def get_run(self, run_id: str) -> RunRecord | None:
        """The run with *run_id*, or None if there is none."""

    def list_runs(
        self,
        *,
        org_id: str,
        project_id: str | None = None,
        limit: int | None = DEFAULT_RUN_LIMIT,
        include_deleted: bool = False,
    ) -> list[RunRecord]:
        """An org's runs, newest first, capped at *limit*; ``None`` means unbounded (the per-project
        `ProjectRegistry.run_ids` partition, which promises *all* of a project's runs). Only
        *project_id*'s when given (BE-0225). Soft-deleted runs are excluded unless *include_deleted*
        (BE-0239)."""

    def soft_delete_run(
        self, run_id: str, *, org_id: str, deleted_by: str | None, at: datetime
    ) -> bool:
        """Mark the org's run *run_id* trashed at *at* by *deleted_by* (BE-0239). True when a live
        run was trashed, False when there was none or it was already trashed (org-scoped, so another
        org's run is untouched — a not-found)."""

    def restore_run(self, run_id: str, *, org_id: str) -> bool:
        """Clear the org's run *run_id*'s soft-delete marker (BE-0239). True when a trashed run was
        restored, False otherwise."""

    def purge_run(self, run_id: str, *, org_id: str) -> bool:
        """Delete the org's run *run_id* row outright (BE-0239). True when a row was removed. The
        audit-log entry keyed on the run id survives, so "who purged run X, when" stays answerable
        without this row."""

    def list_deleted_runs(self, *, org_id: str, before: datetime) -> list[RunRecord]:
        """The org's soft-deleted runs trashed at or before *before* — the retention sweep's DB-side
        eligibility scan (BE-0239). Reaches a run trashed only in the DB (soft-deleted before any
        evidence upload, so it never got a store tombstone) that the store-side scan misses."""

    def create_project(self, project: ProjectRecord) -> None:
        """Register *project*, or update it in place when its id already exists (BE-0225).

        Merges by id, so re-registering the same id rebinds it. Registering a *fresh* id for a
        name the org already uses breaks the ``(org_id, name)`` uniqueness and raises — each
        backend maps this to its own error, so a caller must resolve the existing id via
        ``get_project`` first rather than relying on this to upsert by name.
        """

    def get_project(self, *, org_id: str, name: str) -> ProjectRecord | None:
        """The org's project named *name*, or None if there is none (org-scoped, BE-0225)."""

    def list_projects(self, *, org_id: str) -> list[ProjectRecord]:
        """An org's registered projects, ordered by name (BE-0225)."""

    def delete_project(self, *, org_id: str, name: str) -> None:
        """Deregister the org's project named *name*; its run history is retained (BE-0225)."""

    def ensure_org(self, org_id: str, *, slug: str, name: str) -> None:
        """Create the org if it does not exist yet (idempotent) — 7c-1's single default org.

        Deliberately still create-only (BE-0375): sign-in and job completion call it on every
        request with no membership to pass, so widening it into a create-or-update would let the
        next sign-in clear membership an admin set. `seed_org_membership` writes membership instead.
        """

    def list_orgs(self, *, include_deleted: bool = False) -> list[OrgRecord]:
        """Every org, ordered by slug; soft-deleted ones only with *include_deleted* (BE-0375)."""

    def get_org(self, org_id: str, *, include_deleted: bool = False) -> OrgRecord | None:
        """The org with *org_id*, or None — a soft-deleted one only with *include_deleted*."""

    def create_org(self, *, slug: str, name: str) -> bool:
        """Create an admin-managed org with empty membership, marked seeded (BE-0375).

        The row's id is its slug, matching what every existing writer already carries as `org_id`.
        Marked seeded at creation so no later `orgs:` entry for the same slug can overwrite the
        membership an admin sets. False when the slug is already taken — including by a soft-deleted
        row, which still occupies the UNIQUE constraint; reactivating one is a separate operation
        this seam does not offer.
        """

    def set_org_membership(
        self,
        org_id: str,
        *,
        members: list[str],
        github_orgs: list[str],
        github_teams: list[str],
        editor_teams: list[str],
    ) -> bool:
        """Replace a live org's membership as one unit (BE-0375). False when there is no such org.

        Stamps `membership_seeded_at` when it is not yet set: an API write is a cutover event just
        as creation is, so no later `orgs:` entry can seed over what an admin set here.
        """

    def seed_org_membership(
        self,
        org_id: str,
        *,
        slug: str,
        name: str,
        members: list[str],
        github_orgs: list[str],
        github_teams: list[str],
        editor_teams: list[str],
    ) -> bool:
        """Seed an org's membership from a bound config's `orgs:` entry, once (BE-0375).

        Creates the row when it does not exist, or fills in one `ensure_org` left holding nothing
        but an id, a slug, and a name; either way it stamps `membership_seeded_at`, after which the
        database owns that org's membership and this is a no-op. Returns whether it seeded. A row
        already marked seeded, and a soft-deleted one, are both left alone — retired, not unseeded.
        """

    def soft_delete_org(self, org_id: str, *, at: datetime) -> bool:
        """Mark the org deleted at *at* (BE-0375). False when there is none, or it already was.

        A soft delete, not a row removal: `users`, `runs`, `secrets`, `provider_settings`, and
        `audit_log` still hold foreign keys on this id — including the delete's own audit entry.
        """

    def list_org_user_ids(self, org_id: str) -> list[str]:
        """Every user id recorded under *org_id* — whose sessions retiring the org revokes (BE-0375).

        Read before the soft delete, not after: the delete leaves `users.org_id` pointing at the
        retired slug, so the set is the same either way, but reading first keeps the caller from
        depending on that.
        """

    def upsert_user(
        self, user_id: str, *, org_id: str, github_login: str, email: str, role: str = "editor"
    ) -> None:
        """Insert the user, or update it in place when its id already exists (an OAuth re-login),
        setting its *role* (recomputed from policy each login, BE-0015 7c-2).

        A re-login that lands the user in a *different* org also clears the marker saying they picked
        their active org themselves: the pick was for the org they have left.
        """

    def user_role(self, user_id: str) -> str | None:
        """The user's role (viewer/editor/admin), or None if there is no such user."""

    def user_org(self, user_id: str) -> str | None:
        """The user's org id, or None if there is no such user (BE-0015 multi-tenancy)."""

    def set_user_orgs(self, user_id: str, memberships: dict[str, str]) -> None:
        """Replace the set of orgs *user_id* may act as, mapping each org id to its role.

        A wholesale replacement rather than a merge, so losing a GitHub organization or Team takes
        effect on the next sign-in with no data migration — the same self-healing rule the role
        itself follows (BE-0313). An empty mapping clears the set.
        """

    def list_user_orgs(self, user_id: str) -> dict[str, str]:
        """The orgs *user_id* may act as, each mapped to the role held there. Empty for an unknown
        user, and for one whose only admission came from the admin-Team bypass (BE-0352), which no
        org's membership records."""

    def user_selected_org(self, user_id: str) -> str | None:
        """The org *user_id* picked themselves, or None when the active org was merely resolved for
        them at sign-in. Sign-in preserves a picked org and re-resolves an unpicked one, so the two
        cases must stay distinguishable."""

    def select_active_org(self, user_id: str, org_id: str, *, role: str) -> bool:
        """Make *org_id* the user's active org with *role*, and mark the choice as theirs.

        False when there is no such user. Authorization is the caller's: this writes the choice it
        is given, the way `upsert_user` writes the role it is given.
        """

    def record_audit(
        self, *, org_id: str, actor_id: str | None, action: str, target: str, detail: dict[str, Any]
    ) -> None:
        """Append an audit-log entry — who did what to which target, and when (server clock)."""

    def enqueue_job(
        self, job_id: str, org_id: str, spec: dict[str, Any], capabilities: Iterable[str] = ()
    ) -> None:
        """Insert a job with status ``queued`` and its required-capability routing key (BE-0166)."""

    def register_worker(self, worker_id: str, capabilities: Iterable[str]) -> None:
        """Record what *worker_id* can serve and that it is live now (BE-0166 routing).

        Called on every lease poll — including an empty-queue poll — so an idle worker still refreshes
        its liveness and keeps counting toward what the pool can route (else its jobs would look
        unroutable). Idempotent upsert keyed by *worker_id*.
        """

    def lease_job(self, worker_id: str, capabilities: Iterable[str] = ()) -> LeasedJob | None:
        """Atomically lease the oldest queued job *worker_id* can serve, or return None (BE-0166).

        A job is a candidate only when its required-capability set is a subset of *capabilities* —
        so a worker never leases a job it cannot run. A job no live worker can serve simply stays
        queued (surfaced as unroutable via `metrics_snapshot`), never leased to an incompatible one.
        """

    def touch_worker(self, worker_id: str) -> None:
        """Refresh *worker_id*'s liveness without changing its capabilities (BE-0166 routing).

        A worker polls `lease` only between jobs, so a worker busy on a run longer than the lease
        timeout would otherwise age out of the live set and make its capability's queued jobs look
        unroutable. The heartbeat calls this so a busy worker stays counted as live. A no-op if the
        worker has no registry row yet (it registers on its first lease before any heartbeat).
        """

    def heartbeat_job(self, job_id: str, worker_id: str) -> bool:
        """Renew a lease's timer, returning False when *worker_id* no longer owns the live lease.

        The worker calls this on an interval during a run so a legitimately long run is not
        reclaimed; a False answer tells the worker its lease was reclaimed (or the job finished) and
        it should stop.
        """

    def reclaim_expired_leases(
        self, timeout: timedelta, *, max_attempts: int = DEFAULT_LEASE_MAX_ATTEMPTS
    ) -> list[str]:
        """Re-queue leases with no heartbeat within *timeout*; fail the ones past *max_attempts*.

        Returns the ids re-queued (available again). A worker that dies mid-run stops heart-beating,
        so its lease ages past the timeout and returns to ``queued`` for another worker — but a
        poison job that keeps killing its worker is failed once it hits the attempt cap.
        """

    def complete_job(
        self, job_id: str, result: dict[str, Any], *, worker_id: str | None = None
    ) -> bool:
        """Mark a still-leased job ``done`` with its *result*; False if it is no longer leasable.

        A reclaimed, re-leased, or already-finished job rejects the write (when *worker_id* is
        given, only that leaseholder may complete it), so a stale worker never overwrites the winner.
        """

    def fail_job(self, job_id: str, error: str, *, worker_id: str | None = None) -> bool:
        """Mark a still-leased job ``failed`` with *error*; False if it is no longer leasable (see
        `complete_job`)."""

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return the job's status, result, org_id, and current lease holder (``leased_by``), or None
        if it does not exist."""

    def metrics_snapshot(self) -> JobMetrics:
        """A one-pass aggregate of the jobs table for the ``/metrics`` endpoint (BE-0169)."""

    def save_batch_run_arn(self, job_id: str, run_arn: str) -> None:
        """Persist the scheduled Device Farm run ARN for *job_id* (BE-0336 Unit 5).

        A worker records the ARN the moment the cloud-batch run is scheduled, so a worker that
        re-leases the job after a restart resumes polling that run rather than resubmitting. A no-op
        if the job row is gone.
        """

    def load_batch_run_arn(self, job_id: str) -> str | None:
        """The scheduled Device Farm run ARN persisted for *job_id*, or None (BE-0336 Unit 5).

        None when the run is not yet scheduled, or the job does not exist — the caller then submits a
        fresh run rather than resuming.
        """


def _to_project(row: Project) -> ProjectRecord:
    return ProjectRecord(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        source=dict(row.source) if row.source is not None else None,
        created_at=row.created_at,
    )


def _to_org(row: Org) -> OrgRecord:
    # The membership columns are nullable — an org row that predates BE-0375, or one `ensure_org`
    # created at sign-in, holds NULL rather than `[]` (the model's `default` is Python-side only, so
    # it never reached those rows). Normalize here, once, so no reader has to.
    return OrgRecord(
        id=row.id,
        slug=row.slug,
        name=row.name,
        members=list(row.members or []),
        github_orgs=list(row.github_orgs or []),
        github_teams=list(row.github_teams or []),
        editor_teams=list(row.editor_teams or []),
        membership_seeded_at=row.membership_seeded_at,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


def _to_record(row: Run) -> RunRecord:
    return RunRecord(
        id=row.id,
        org_id=row.org_id,
        status=row.status,
        project_id=row.project_id,
        created_by=row.created_by,
        ok=row.ok,
        created_at=row.created_at,
        summary=dict(row.summary),
        scenario_hash=row.scenario_hash,
        tool_version=row.tool_version,
        git_revision=row.git_revision,
        device_runtime=row.device_runtime,
        deleted_at=row.deleted_at,
        deleted_by=row.deleted_by,
    )


class SqlRepository:
    """A SQLAlchemy-backed `Repository`. Works against any engine SQLAlchemy supports — SQLite on
    the gate, Postgres in production — since the models pick JSONB only on Postgres."""

    def __init__(
        self,
        engine: Engine,
        *,
        lease_timeout: timedelta | None = None,
        max_attempts: int = DEFAULT_LEASE_MAX_ATTEMPTS,
    ) -> None:
        self._engine = engine
        self._lease_timeout = lease_timeout or timedelta(seconds=DEFAULT_LEASE_TIMEOUT_SECONDS)
        self._max_attempts = max_attempts

    def record_run(self, run: RunRecord) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        # `merge` upserts by primary key, so re-recording a run (e.g. a status change) updates it
        # rather than colliding. `created_at` is left to the server default unless given.
        fields: dict[str, Any] = {
            "id": run.id,
            "org_id": run.org_id,
            "status": run.status,
            "project_id": run.project_id,
            "created_by": run.created_by,
            "ok": run.ok,
            "summary": run.summary,
            "scenario_hash": run.scenario_hash,
            "tool_version": run.tool_version,
            "git_revision": run.git_revision,
            "device_runtime": run.device_runtime,
        }
        if run.created_at is not None:
            fields["created_at"] = run.created_at
        with Session(self._engine) as session:
            session.merge(Run(**fields))
            session.commit()

    def get_run(self, run_id: str) -> RunRecord | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        with Session(self._engine) as session:
            row = session.get(Run, run_id)
            return _to_record(row) if row is not None else None

    def list_runs(
        self,
        *,
        org_id: str,
        project_id: str | None = None,
        limit: int | None = DEFAULT_RUN_LIMIT,
        include_deleted: bool = False,
    ) -> list[RunRecord]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        stmt = select(Run).where(Run.org_id == org_id)
        if project_id is not None:
            stmt = stmt.where(Run.project_id == project_id)
        if not include_deleted:
            stmt = stmt.where(
                Run.deleted_at.is_(None)
            )  # trashed runs drop out of history (BE-0239)
        stmt = stmt.order_by(Run.created_at.desc()).limit(limit)
        with Session(self._engine) as session:
            return [_to_record(row) for row in session.scalars(stmt)]

    def soft_delete_run(
        self, run_id: str, *, org_id: str, deleted_by: str | None, at: datetime
    ) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        # Org-scoped and "was live": a cross-org id or an already-trashed run is a clean not-found
        # (False). The in-place update mirrors `upsert_user`, so no ambiguous `rowcount` is needed.
        with Session(self._engine) as session:
            run = session.get(Run, run_id)
            if run is None or run.org_id != org_id or run.deleted_at is not None:
                return False
            run.deleted_at, run.deleted_by = at, deleted_by
            session.commit()
            return True

    def restore_run(self, run_id: str, *, org_id: str) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        with Session(self._engine) as session:
            run = session.get(Run, run_id)
            if run is None or run.org_id != org_id or run.deleted_at is None:
                return False
            run.deleted_at, run.deleted_by = None, None
            session.commit()
            return True

    def purge_run(self, run_id: str, *, org_id: str) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        with Session(self._engine) as session:
            run = session.get(Run, run_id)
            if run is None or run.org_id != org_id:
                return False
            session.delete(run)
            session.commit()
            return True

    def list_deleted_runs(self, *, org_id: str, before: datetime) -> list[RunRecord]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        stmt = select(Run).where(
            Run.org_id == org_id, Run.deleted_at.is_not(None), Run.deleted_at <= before
        )
        with Session(self._engine) as session:
            return [_to_record(row) for row in session.scalars(stmt)]

    def create_project(self, project: ProjectRecord) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Project

        # `merge` upserts by primary key, so re-registering a project (e.g. rebinding its source)
        # updates it rather than colliding — the same idempotent shape as `record_run`. A fresh
        # id reusing an existing `(org_id, name)` breaks the unique constraint; this backend
        # surfaces the Protocol's documented collision as SQLAlchemy's `IntegrityError`.
        # `source` and `created_at` are only injected when non-None: a caller that doesn't
        # re-supply them (e.g. a rename-only update) must not clobber an existing binding or
        # the DB-generated timestamp — same guard pattern as `record_run` with `created_at`.
        fields: dict[str, Any] = {
            "id": project.id,
            "org_id": project.org_id,
            "name": project.name,
        }
        if project.source is not None:
            fields["source"] = project.source
        if project.created_at is not None:
            fields["created_at"] = project.created_at
        with Session(self._engine) as session:
            session.merge(Project(**fields))
            session.commit()

    def get_project(self, *, org_id: str, name: str) -> ProjectRecord | None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Project

        stmt = select(Project).where(Project.org_id == org_id, Project.name == name)
        with Session(self._engine) as session:
            row = session.scalars(stmt).first()
            return _to_project(row) if row is not None else None

    def list_projects(self, *, org_id: str) -> list[ProjectRecord]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Project

        stmt = select(Project).where(Project.org_id == org_id).order_by(Project.name)
        with Session(self._engine) as session:
            return [_to_project(row) for row in session.scalars(stmt)]

    def delete_project(self, *, org_id: str, name: str) -> None:
        from sqlalchemy import delete
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Project

        # Only the binding is removed; the run history is retained (BE-0225). On Postgres, the FK's
        # ON DELETE SET NULL (migration 0010) clears `runs.project_id` on the retained rows; on the
        # SQLite gate (FKs unenforced by default) it stays pointing at the now-deregistered id.
        with Session(self._engine) as session:
            session.execute(delete(Project).where(Project.org_id == org_id, Project.name == name))
            session.commit()

    def ensure_org(self, org_id: str, *, slug: str, name: str) -> None:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            if session.get(Org, org_id) is not None:
                return
            session.add(Org(id=org_id, slug=slug, name=name))  # leave created_at to the default
            try:
                session.commit()
            except IntegrityError:
                # A concurrent login inserted it between the check and the commit — that's the
                # idempotent outcome we wanted, so swallow it.
                session.rollback()

    def list_orgs(self, *, include_deleted: bool = False) -> list[OrgRecord]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        stmt = select(Org)
        if not include_deleted:
            stmt = stmt.where(Org.deleted_at.is_(None))
        stmt = stmt.order_by(Org.slug)
        with Session(self._engine) as session:
            return [_to_org(row) for row in session.scalars(stmt)]

    def get_org(self, org_id: str, *, include_deleted: bool = False) -> OrgRecord | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            row = session.get(Org, org_id)
            if row is None or (row.deleted_at is not None and not include_deleted):
                return None
            return _to_org(row)

    def create_org(self, *, slug: str, name: str) -> bool:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            if session.get(Org, slug) is not None:
                return False
            session.add(
                Org(
                    id=slug,
                    slug=slug,
                    name=name,
                    members=[],
                    github_orgs=[],
                    github_teams=[],
                    # Seeded at creation, so a later `orgs:` entry for this slug never seeds over
                    # the membership an admin sets through the API (BE-0375).
                    membership_seeded_at=datetime.now(UTC),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                # A concurrent create (or a soft-deleted row still holding the UNIQUE slug that the
                # `get` above raced) — either way the slug is taken, which is this method's False.
                session.rollback()
                return False
            return True

    def set_org_membership(
        self,
        org_id: str,
        *,
        members: list[str],
        github_orgs: list[str],
        github_teams: list[str],
        editor_teams: list[str],
    ) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            row = session.get(Org, org_id)
            if row is None or row.deleted_at is not None:
                return False
            row.members, row.github_orgs = members, github_orgs
            row.github_teams, row.editor_teams = github_teams, editor_teams
            if row.membership_seeded_at is None:
                # An admin can reach a row the backfill never marked — one `ensure_org` created at
                # sign-in, one predating the migration, or one left unseeded because the config
                # failed to load at boot. Mark it now, or the next startup or rebind would find it
                # unseeded and replace this roster with the `orgs:` entry's: exactly the overwrite
                # the per-row marker exists to prevent, arriving through the admin's own edit.
                row.membership_seeded_at = datetime.now(UTC)
            session.commit()
            return True

    def seed_org_membership(
        self,
        org_id: str,
        *,
        slug: str,
        name: str,
        members: list[str],
        github_orgs: list[str],
        github_teams: list[str],
        editor_teams: list[str],
    ) -> bool:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            row = session.get(Org, org_id)
            if row is not None and (
                row.membership_seeded_at is not None or row.deleted_at is not None
            ):
                return False  # past cutover, or retired — either way config no longer decides it
            seeded_at = datetime.now(UTC)
            if row is None:
                session.add(
                    Org(
                        id=org_id,
                        slug=slug,
                        name=name,
                        members=members,
                        github_orgs=github_orgs,
                        github_teams=github_teams,
                        editor_teams=editor_teams,
                        membership_seeded_at=seeded_at,
                    )
                )
                try:
                    session.commit()
                except IntegrityError:
                    # A concurrent sign-in inserted the passive row first; fall through and fill it.
                    session.rollback()
                    row = session.get(Org, org_id)
                    # The same "seeded or retired" guard the check above applies: the row that won
                    # the race may have been soft-deleted since, and a retired org is not unseeded.
                    if (
                        row is None
                        or row.membership_seeded_at is not None
                        or row.deleted_at is not None
                    ):
                        return False
                else:
                    return True
            row.members, row.github_orgs = members, github_orgs
            row.github_teams, row.editor_teams = github_teams, editor_teams
            row.membership_seeded_at = seeded_at
            session.commit()
            return True

    def soft_delete_org(self, org_id: str, *, at: datetime) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            row = session.get(Org, org_id)
            if row is None or row.deleted_at is not None:
                return False
            row.deleted_at = at
            session.commit()
            return True

    def list_org_user_ids(self, org_id: str) -> list[str]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        stmt = select(User.id).where(User.org_id == org_id)
        with Session(self._engine) as session:
            return list(session.scalars(stmt))

    def upsert_user(
        self, user_id: str, *, org_id: str, github_login: str, email: str, role: str = "editor"
    ) -> None:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            if user is None:
                session.add(
                    User(
                        id=user_id,
                        org_id=org_id,
                        github_login=github_login,
                        email=email,
                        role=role,
                    )
                )
                try:
                    session.commit()
                except IntegrityError:
                    # A concurrent OAuth callback inserted the same user first; fall through to
                    # update the now-existing row instead of failing the login.
                    session.rollback()
                    user = session.get(User, user_id)
                else:
                    return
            if user is not None:  # update in place (a re-login) without disturbing created_at
                if user.org_id != org_id:
                    # This sign-in moved the user to a different org, so any org they had picked
                    # themselves is no longer the one they are in — the marker would otherwise
                    # claim the new org as their choice and pin them to it on every later sign-in.
                    user.org_selected_at = None
                user.org_id, user.github_login, user.email, user.role = (
                    org_id,
                    github_login,
                    email,
                    role,
                )
                session.commit()

    def user_role(self, user_id: str) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            return user.role if user is not None else None

    def user_org(self, user_id: str) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            return user.org_id if user is not None else None

    def set_user_orgs(self, user_id: str, memberships: dict[str, str]) -> None:
        from sqlalchemy import delete
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import UserOrg

        with Session(self._engine) as session:
            session.execute(delete(UserOrg).where(UserOrg.user_id == user_id))
            session.add_all(
                UserOrg(user_id=user_id, org_id=org_id, role=role)
                for org_id, role in memberships.items()
            )
            # One transaction, so a concurrent read never sees the gap between the clear and the
            # rewrite — which would be an empty eligible set, and so a refused switch.
            try:
                session.commit()
            except IntegrityError:
                # A concurrent sign-in for the same login committed the same rows first (two tabs,
                # or two replicas over one database). They come from the same GitHub identity
                # moments apart, so letting theirs stand is the idempotent outcome — the same race
                # `ensure_org` and `upsert_user` already swallow on this path.
                session.rollback()

    def list_user_orgs(self, user_id: str) -> dict[str, str]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org, UserOrg

        # Joined against `orgs` so a retired tenant drops out of the eligible set the same way it
        # drops out of sign-in resolution, and ordered by slug so the selector's order is stable.
        stmt = (
            select(UserOrg.org_id, UserOrg.role)
            .join(Org, Org.id == UserOrg.org_id)
            .where(UserOrg.user_id == user_id, Org.deleted_at.is_(None))
            .order_by(Org.slug)
        )
        with Session(self._engine) as session:
            # A `Row` unpacks like the 2-tuple it is, which `dict` accepts and mypy's stub for
            # `Sequence[Row[...]]` does not describe.
            return dict(session.execute(stmt).all())  # type: ignore[arg-type]

    def user_selected_org(self, user_id: str) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            if user is None or user.org_selected_at is None:
                return None
            return user.org_id

    def select_active_org(self, user_id: str, org_id: str, *, role: str) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import User

        with Session(self._engine) as session:
            user = session.get(User, user_id)
            if user is None:
                return False
            user.org_id, user.role, user.org_selected_at = org_id, role, datetime.now(UTC)
            session.commit()
            return True

    def record_audit(
        self, *, org_id: str, actor_id: str | None, action: str, target: str, detail: dict[str, Any]
    ) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import AuditLog

        with Session(self._engine) as session:
            session.add(
                AuditLog(
                    id=uuid.uuid4().hex,
                    org_id=org_id,
                    actor_id=actor_id,
                    action=action,
                    target=target,
                    detail=detail,
                )
            )
            session.commit()

    def enqueue_job(
        self, job_id: str, org_id: str, spec: dict[str, Any], capabilities: Iterable[str] = ()
    ) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            session.add(
                JobRecord(id=job_id, org_id=org_id, spec=spec, capabilities=list(capabilities))
            )
            session.commit()

    def register_worker(self, worker_id: str, capabilities: Iterable[str]) -> None:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import WorkerRecord

        caps = list(capabilities)
        now = datetime.now(UTC)
        # Insert-or-update keyed by worker_id (same pattern as `upsert_user`): a plain
        # `SELECT ... FOR UPDATE` can't serialize two *first-ever* polls for the same id — it takes
        # no gap lock — so a concurrent insert (a client retry, or two replicas briefly sharing an
        # explicit --worker-id) would make the second commit raise. Catch that and fall through to
        # the update branch instead of crashing the lease poll.
        with Session(self._engine) as session:
            row = session.get(WorkerRecord, worker_id)
            if row is None:
                session.add(WorkerRecord(id=worker_id, capabilities=caps, last_seen=now))
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    row = session.get(WorkerRecord, worker_id)
                else:
                    return
            if row is not None:  # update in place (last-writer-wins on caps + last_seen)
                row.capabilities = caps
                row.last_seen = now
                session.commit()

    def lease_job(self, worker_id: str, capabilities: Iterable[str] = ()) -> LeasedJob | None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.capabilities import can_serve
        from bajutsu.serve.server.models import JobRecord

        # Sweep dead workers' leases back into the queue before serving, so a stuck job is picked up
        # on the next poll without a separate reaper process.
        self.reclaim_expired_leases(self._lease_timeout, max_attempts=self._max_attempts)
        advertised = set(capabilities)
        with Session(self._engine) as session:
            # Scan queued jobs oldest-first for the first this worker can serve — capability filtering
            # can't be a `.limit(1)` because the oldest queued job may need a capability this worker
            # lacks, and skipping it must still find a younger servable one (the `status` index bounds
            # the scan to queued rows). Only (id, capabilities) is read up front, taking no locks, so a
            # capability-skipped row is never locked (which would starve a concurrent leaser). The
            # chosen candidate is then locked on its own — `FOR UPDATE SKIP LOCKED` on that single row —
            # and re-checked for `queued`: if another worker took it between the scan and the lock, the
            # row reads as gone/leased and this worker moves on to the next candidate.
            #
            # The scan is deliberately unbounded rather than capped at the oldest N: an unroutable
            # backlog piles up at the *head* (oldest), so a fixed N would let it hide a servable
            # younger job forever — the same starvation `.limit(1)` has. The only bounded-and-correct
            # alternative pushes the subset test into SQL (Postgres JSONB `<@`), which the SQLite gate
            # can't exercise. Kept simple for the intended small self-hosted pool, where the backlog is
            # a misconfiguration `bajutsu_unroutable_jobs` surfaces; keyset pagination is the escalation
            # if a deep-queue / large-fleet deployment ever needs it.
            candidates = session.execute(
                select(JobRecord.id, JobRecord.capabilities)
                .where(JobRecord.status == "queued")
                .order_by(JobRecord.created_at)
            ).all()
            for job_id, caps in candidates:
                if not can_serve(caps or [], advertised):
                    continue
                stmt = select(JobRecord).where(JobRecord.id == job_id, JobRecord.status == "queued")
                if self._engine.dialect.name != "sqlite":
                    stmt = stmt.with_for_update(skip_locked=True)
                row = session.scalars(stmt).first()
                if row is None:  # taken (or locked) by another worker since the scan — try the next
                    continue
                row.status = "leased"
                row.leased_at = datetime.now(UTC)
                row.leased_by = worker_id
                leased = LeasedJob(id=row.id, org_id=row.org_id, spec=dict(row.spec))
                session.commit()
                return leased
            return None

    def touch_worker(self, worker_id: str) -> None:
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import WorkerRecord

        with Session(self._engine) as session:
            session.execute(
                update(WorkerRecord)
                .where(WorkerRecord.id == worker_id)
                .values(last_seen=datetime.now(UTC))
            )
            session.commit()

    def heartbeat_job(self, job_id: str, worker_id: str) -> bool:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            # Lock the row so a heartbeat and a concurrent reclaim serialize instead of racing: the
            # loser re-reads fresh state under the lock, so a heartbeat that lands after a reclaim
            # sees `queued` and returns False rather than resurrecting `leased_at` on a re-queued job.
            stmt = select(JobRecord).where(JobRecord.id == job_id)
            if self._engine.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
            row = session.scalars(stmt).first()
            if row is None or row.status != "leased" or row.leased_by != worker_id:
                return False
            row.leased_at = datetime.now(UTC)
            session.commit()
            return True

    def reclaim_expired_leases(
        self, timeout: timedelta, *, max_attempts: int = DEFAULT_LEASE_MAX_ATTEMPTS
    ) -> list[str]:
        from sqlalchemy import delete, select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord, WorkerRecord

        cutoff = datetime.now(UTC) - timeout
        requeued: list[str] = []
        with Session(self._engine) as session:
            # Prune dead workers in the same sweep the leases use — a worker not seen within the
            # timeout is dead by the same definition, so its registry row stops counting toward
            # routability and the table stays bounded to the live pool (BE-0166), never leaking a row
            # per restarted `worker-<pid>`.
            session.execute(delete(WorkerRecord).where(WorkerRecord.last_seen < cutoff))
            stmt = select(JobRecord).where(
                JobRecord.status == "leased", JobRecord.leased_at < cutoff
            )
            # Skip rows a concurrent heartbeat is holding: that worker is alive and just renewed its
            # lease, so leave it be rather than reclaiming a job out from under it (lost update).
            if self._engine.dialect.name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)
            for row in session.scalars(stmt):
                row.attempts += 1
                row.leased_by = None
                row.leased_at = None
                if row.attempts >= max_attempts:
                    row.status = "failed"
                    row.result = {"error": f"lease expired after {row.attempts} attempts"}
                else:
                    row.status = "queued"
                    requeued.append(row.id)
            session.commit()
        return requeued

    def complete_job(
        self, job_id: str, result: dict[str, Any], *, worker_id: str | None = None
    ) -> bool:
        return self._finish_job(job_id, status="done", payload=result, worker_id=worker_id)

    def fail_job(self, job_id: str, error: str, *, worker_id: str | None = None) -> bool:
        return self._finish_job(
            job_id, status="failed", payload={"error": error}, worker_id=worker_id
        )

    def _finish_job(
        self, job_id: str, *, status: str, payload: dict[str, Any], worker_id: str | None
    ) -> bool:
        """Transition a still-leased job to a terminal *status*, returning False when it may not.

        Only a job still ``leased`` (by *worker_id*, when given) accepts its result; a reclaimed,
        re-leased, or already-finished job rejects the stale write so the winning run is never
        overwritten. Locks the row on non-SQLite so the check-and-write is atomic against reclaim."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            stmt = select(JobRecord).where(JobRecord.id == job_id)
            if self._engine.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
            row = session.scalars(stmt).first()
            if row is None or row.status != "leased":
                return False
            if worker_id is not None and row.leased_by != worker_id:
                return False
            row.status = status
            row.result = payload
            session.commit()
            return True

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            row = session.get(JobRecord, job_id)
            if row is None:
                return None
            return {
                "status": row.status,
                "result": dict(row.result),
                "org_id": row.org_id,
                "leased_by": row.leased_by,
            }

    def save_batch_run_arn(self, job_id: str, run_arn: str) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            row = session.get(JobRecord, job_id)
            if row is None:
                return
            row.batch_state = {"run_arn": run_arn}
            session.commit()

    def load_batch_run_arn(self, job_id: str) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            row = session.get(JobRecord, job_id)
            if row is None:
                return None
            run_arn = (row.batch_state or {}).get("run_arn")
            return str(run_arn) if run_arn is not None else None

    def metrics_snapshot(self) -> JobMetrics:
        from collections import defaultdict

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.capabilities import can_serve
        from bajutsu.serve.server.models import JobRecord, WorkerRecord

        now = datetime.now(UTC)
        queued: dict[str, int] = defaultdict(int)
        leased: dict[str, int] = defaultdict(int)
        # Per worker, keep its freshest lease renewal (max leased_at) — that is its last heartbeat.
        latest_heartbeat: dict[str, datetime] = {}
        oldest_in_flight = 0.0
        queued_caps: list[list[str]] = []  # required-capability set of each queued job (BE-0166)
        # Read only the columns the aggregate needs — never `spec`/`result`, which can carry
        # secrets. `capabilities` is the routing key (no secret), needed for the unroutable count.
        # Filtering to the two live states keeps the read off finished rows.
        stmt = select(
            JobRecord.status,
            JobRecord.org_id,
            JobRecord.leased_by,
            JobRecord.leased_at,
            JobRecord.created_at,
            JobRecord.capabilities,
        ).where(JobRecord.status.in_(("queued", "leased")))
        cutoff = now - self._lease_timeout
        with Session(self._engine) as session:
            for status, org_id, leased_by, leased_at, created_at, caps in session.execute(stmt):
                if status == "queued":
                    queued[org_id] += 1
                    queued_caps.append(list(caps or []))
                    continue
                leased[org_id] += 1
                oldest_in_flight = max(oldest_in_flight, _age_seconds(now, created_at))
                if leased_by is not None and leased_at is not None:
                    fresh = latest_heartbeat.get(leased_by)
                    renewed = _as_utc(leased_at)
                    if fresh is None or renewed > fresh:
                        latest_heartbeat[leased_by] = renewed
            # What the *live* pool can serve: a worker seen within the lease timeout is alive (the
            # same freshness window the reclaim path uses; the heartbeat refreshes `last_seen` so a
            # worker busy on a long run still counts). A queued job is unroutable when no single live
            # worker advertises all of its required capabilities — the same `can_serve` subset test
            # the lease filter uses, so "unroutable" means exactly "no worker would lease it".
            live = [
                list(w.capabilities or [])
                for w in session.scalars(
                    select(WorkerRecord).where(WorkerRecord.last_seen >= cutoff)
                )
            ]
        unroutable = sum(1 for req in queued_caps if not any(can_serve(req, adv) for adv in live))
        return JobMetrics(
            queued_by_org=dict(queued),
            leased_by_org=dict(leased),
            heartbeat_age_by_worker={
                worker: (now - renewed).total_seconds()
                for worker, renewed in latest_heartbeat.items()
            },
            oldest_in_flight_seconds=oldest_in_flight,
            unroutable_queued=unroutable,
        )


def _as_utc(dt: datetime) -> datetime:
    """Read a stored timestamp as UTC-aware. SQLite (the gate) hands back naive datetimes for a
    ``DateTime(timezone=True)`` column, so subtracting a UTC-aware ``now`` would raise; assume UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _age_seconds(now: datetime, then: datetime) -> float:
    return (now - _as_utc(then)).total_seconds()


def engine_from_url(url: str) -> Engine:
    """Build a SQLAlchemy engine for *url* (e.g. ``postgresql://…`` in production, ``sqlite://`` on
    the gate). SQLAlchemy is imported here so the default path never loads it."""
    from sqlalchemy import create_engine

    return create_engine(url)


def _positive_env(name: str, raw: str, *, cast: Any) -> Any:
    """Parse an operator-facing positive-number env var defensively — a clear, variable-named error
    rather than a bare ValueError/TypeError. Non-numeric or non-positive values are rejected."""
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive number, got {raw!r}") from None
    # NaN/inf slip past `<= 0` (NaN compares False, inf is "positive"), so reject them explicitly —
    # a timedelta(seconds=nan) or an infinite cap is not a well-defined operator setting.
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {raw!r}")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def repository_from_env() -> SqlRepository | None:
    """A `SqlRepository` from ``BAJUTSU_DATABASE_URL``, or ``None`` when it is unset — so the
    server backend runs without a database until one is configured, and local never has one. The
    schema itself is owned by Alembic (7a-2), not created here."""
    url = os.environ.get("BAJUTSU_DATABASE_URL")
    if not url:
        return None
    kwargs: dict[str, Any] = {}
    if timeout := os.environ.get("BAJUTSU_LEASE_TIMEOUT_SECONDS"):
        kwargs["lease_timeout"] = timedelta(
            seconds=_positive_env("BAJUTSU_LEASE_TIMEOUT_SECONDS", timeout, cast=float)
        )
    if attempts := os.environ.get("BAJUTSU_LEASE_MAX_ATTEMPTS"):
        kwargs["max_attempts"] = _positive_env("BAJUTSU_LEASE_MAX_ATTEMPTS", attempts, cast=int)
    return SqlRepository(engine_from_url(url), **kwargs)

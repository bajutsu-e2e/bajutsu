"""The SQLAlchemy repository, against SQLite on the gate and Postgres in production."""

from __future__ import annotations

import uuid
import zlib
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ._functions import _age_seconds, _as_utc, _rowcount, _to_org, _to_record
from ._shared import (
    DEFAULT_LEASE_MAX_ATTEMPTS,
    DEFAULT_RUN_LIMIT,
    TERMINAL_JOB_STATUSES,
    cancelled_job_result,
)
from .concurrency_caps import ConcurrencyCaps
from .job_metrics import JobMetrics
from .leased_job import LeasedJob
from .org_record import OrgRecord
from .queued_job import QueuedJob
from .run_record import RunRecord

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import InstrumentedAttribute, Session

    from bajutsu.serve.server.models import JobGroup, JobRecord

# A lease with no heartbeat for this long is treated as a dead worker and reclaimed; a job that is
# reclaimed this many times is failed rather than re-queued forever (BE-0016 worker liveness). The
# worker's heartbeat interval must stay well under the timeout so a live long run is never reclaimed.
DEFAULT_LEASE_TIMEOUT_SECONDS = 120.0

# How many times `cancel_job` retries its queued-then-leased pair when the row moves between the two
# updates. Each retry needs a lease or a reclaim to land in a window of one statement, so a handful
# is far past what a real race produces.
_CANCEL_ATTEMPTS = 5

# The clock skew a spent-`jti` row must outlive, matching `bajutsu.serve.oidc`'s own allowance —
# duplicated as a constant rather than imported so this module stays free of the OIDC one.
_REPLAY_SKEW_SECONDS = 60


def _run_fields(run: RunRecord) -> dict[str, Any]:
    """The `Run` row's columns for *run*; `created_at` is left to the server default unless given."""
    fields: dict[str, Any] = {
        "id": run.id,
        "org_id": run.org_id,
        "status": run.status,
        "created_by": run.created_by,
        "ok": run.ok,
        "summary": run.summary,
        "scenario_hash": run.scenario_hash,
        "tool_version": run.tool_version,
        "git_revision": run.git_revision,
        "device_runtime": run.device_runtime,
        "label": run.label,
        "target": run.target,
        "run_coordinates": run.run_coordinates,
    }
    if run.created_at is not None:
        fields["created_at"] = run.created_at
    return fields


def _reclaimed_outcome(
    job_id: str, attempts: int, *, exhausted: bool, cancelling: bool
) -> dict[str, Any]:
    """The status (and terminal result, if any) a reclaimed lease moves its job to."""
    if cancelling:
        return {"status": "cancelled", "result": cancelled_job_result(job_id)}
    if exhausted:
        return {"status": "failed", "result": {"error": f"lease expired after {attempts} attempts"}}
    return {"status": "queued"}


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
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        # `merge` upserts by primary key, so re-recording a run (e.g. a status change) updates it
        # rather than colliding.
        fields = _run_fields(run)
        with Session(self._engine) as session:
            session.merge(Run(**fields))
            try:
                session.commit()
            except IntegrityError:
                # `merge` selects, then inserts: an insert-only writer (a rescan, BE-0449) can land
                # the same id in between. Retry once; the row now exists, so the merge updates it
                # and this write's actor and label still win.
                session.rollback()
                session.merge(Run(**fields))
                session.commit()

    def record_run_if_absent(self, run: RunRecord) -> bool:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        fields = _run_fields(run)
        with Session(self._engine) as session:
            if session.get(Run, run.id) is not None:
                return False
            session.add(Run(**fields))
            try:
                session.commit()
            except IntegrityError:
                # Another writer inserted the same id between the read and the commit; the primary
                # key turned that race into a no-op, which is the outcome this method promises. Any
                # other constraint (a foreign key) is a real failure, so it is raised, not hidden.
                session.rollback()
                if session.get(Run, run.id) is not None:
                    return False
                raise
        return True

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
        label: str | None = None,
        target: str | None = None,
        limit: int | None = DEFAULT_RUN_LIMIT,
        include_deleted: bool = False,
    ) -> list[RunRecord]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        stmt = select(Run).where(Run.org_id == org_id)
        if label is not None:
            # An unlabeled run matches every label filter (BE-0404 unit 4): a run recorded before
            # the column existed, or one enqueued with no config bound, belongs to no partition, and
            # hiding it would make a deployment's pre-upgrade history vanish the moment its first
            # labeled run lands.
            stmt = stmt.where((Run.label == label) | Run.label.is_(None))
        if target is not None:
            stmt = stmt.where(Run.target == target)
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

    def set_org_config_source(self, org_id: str, source: dict[str, Any]) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            row = session.get(Org, org_id)
            if row is None or row.deleted_at is not None:
                return False
            row.config_source = source
            session.commit()
            return True

    def set_org_membership(
        self,
        org_id: str,
        *,
        members: list[str],
        github_orgs: list[str],
        github_teams: list[str],
        editor_teams: list[str],
        allowed_repositories: list[dict[str, Any]] | None = None,
    ) -> bool:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Org

        with Session(self._engine) as session:
            row = session.get(Org, org_id)
            if row is None or row.deleted_at is not None:
                return False
            row.members, row.github_orgs = members, github_orgs
            row.github_teams, row.editor_teams = github_teams, editor_teams
            if allowed_repositories is not None:  # None = leave the machine roster alone
                row.allowed_repositories = allowed_repositories
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
        allowed_repositories: list[dict[str, Any]] | None = None,
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
                        allowed_repositories=allowed_repositories,
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
            if allowed_repositories is not None:  # None leaves it alone, as `set_org_membership`
                row.allowed_repositories = allowed_repositories
            row.membership_seeded_at = seeded_at
            session.commit()
            return True

    def spend_oidc_jti(self, jti: str, *, expires_at: datetime) -> bool:
        from sqlalchemy import delete
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import OidcJti

        # Swept behind the same clock-skew allowance the lifetime checks grant (`oidc`'s
        # `_CLOCK_SKEW_SECONDS`), not behind `now`. A token stays acceptable while `exp >= now -
        # skew`, so sweeping at `now` would drop a row while the token it names could still be
        # presented — leaving single-use resting on the exchange's separate born-dead guard
        # instead of on this table. Keeping the row for the whole window the token is live makes
        # the table sufficient on its own.
        swept_before = datetime.now(UTC) - timedelta(seconds=_REPLAY_SKEW_SECONDS)
        with Session(self._engine) as session:
            # Swept here rather than on a schedule: an exchange is the only writer, so the table
            # cannot grow between two of them, and this keeps the mechanism in one place. It
            # commits on its own, ahead of the insert: sharing the insert's transaction would let
            # the rollback below discard the sweep too, and under a replay flood — where every
            # call conflicts — the table would then never shrink at all.
            session.execute(delete(OidcJti).where(OidcJti.expires_at < swept_before))
            session.commit()
            session.add(OidcJti(id=jti, expires_at=expires_at))
            try:
                session.commit()
            except IntegrityError:
                # The primary key is the whole single-use rule: whoever inserted first spent the
                # token, and this caller — a replay, or the losing side of a race between two
                # replicas — is refused. The table carries no other constraint that could reject
                # an insert, so a duplicate key is the only thing this can mean.
                session.rollback()
                return False
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
        self,
        job_id: str,
        org_id: str,
        spec: dict[str, Any],
        capabilities: Iterable[str] = (),
        *,
        actor: str | None = None,
    ) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            session.add(
                JobRecord(
                    id=job_id,
                    org_id=org_id,
                    spec=spec,
                    capabilities=list(capabilities),
                    actor=actor,
                )
            )
            session.commit()

    def register_job_group(
        self,
        group_id: str,
        *,
        org_id: str,
        key: str,
        digest: str,
        compat_key: str | None,
        jobs: Sequence[QueuedJob],
    ) -> str | None:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        from bajutsu.serve.capabilities import base_key, route_key
        from bajutsu.serve.server.models import JobGroup, JobRecord

        with Session(self._engine) as session:
            held = self._group_by_key(session, org_id, key)
            if held is not None:
                return held.id if held.digest == digest else None
            session.add(JobGroup(id=group_id, org_id=org_id, key=key, digest=digest))
            session.add_all(
                JobRecord(
                    id=job.id,
                    org_id=org_id,
                    spec=job.spec,
                    capabilities=list(job.capabilities),
                    actor=job.actor,
                    group_id=group_id,
                    compat_key=compat_key,
                    route_key=route_key(job.capabilities),
                    base_key=base_key(job.capabilities),
                )
                for job in jobs
            )
            try:
                session.commit()
            except IntegrityError:
                # A concurrent request took the key between the read and the commit; any other
                # integrity error leaves no group under the key and is the caller's to see.
                session.rollback()
                held = self._group_by_key(session, org_id, key)
                if held is None:
                    raise
                return held.id if held.digest == digest else None
        return group_id

    @staticmethod
    def _group_by_key(session: Session, org_id: str, key: str) -> JobGroup | None:
        from sqlalchemy import select

        from bajutsu.serve.server.models import JobGroup

        return session.scalars(
            select(JobGroup).where(JobGroup.org_id == org_id, JobGroup.key == key)
        ).first()

    def get_job_group(self, group_id: str) -> dict[str, Any] | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobGroup

        with Session(self._engine) as session:
            row = session.get(JobGroup, group_id)
            return None if row is None else {"id": row.id, "org_id": row.org_id, "key": row.key}

    def group_jobs(self, group_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        # Jobs of one request often share a `created_at` second on SQLite; ids are the registry's
        # counter, so ordering by length then text keeps "9" ahead of "10".
        query = (
            select(JobRecord.id, JobRecord.status, JobRecord.result)
            .where(JobRecord.group_id == group_id)
            .order_by(JobRecord.created_at, func.length(JobRecord.id), JobRecord.id)
        )
        with Session(self._engine) as session:
            return [
                {"id": i, "status": status, "result": result}
                for i, status, result in session.execute(query)
            ]

    def max_job_id(self) -> int:
        # Pulls every job id into Python to find the max — fine for a once-per-boot startup query,
        # but would need SQL-side MAX(CAST(id AS INTEGER)) if `jobs` ever grows large enough for
        # that to matter (it has no retention sweep of its own today).
        from sqlalchemy import inspect, select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        # Called once from server-backend startup, before this process is guaranteed the migration
        # that creates `jobs` has run against this database yet — the id counter then simply starts
        # at 0, same as before this seeding existed. Checking existence directly (rather than
        # catching the query's error) means a database that's merely unreachable at boot still
        # raises here instead of silently seeding 0 and reintroducing the same duplicate-key
        # collision once it comes back — `has_table` itself raises when the connection fails.
        if not inspect(self._engine).has_table(JobRecord.__tablename__):
            return 0
        with Session(self._engine) as session:
            ids = session.scalars(select(JobRecord.id)).all()
        # `id` is a str column (non-numeric ids existed before BE-0166 routing); skip any that
        # aren't a plain int rather than letting one bad row crash the whole startup seed.
        numeric = [int(i) for i in ids if i.isdigit()]
        return max(numeric, default=0)

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
        leased = self.lease_jobs(worker_id, capabilities)
        return leased[0] if leased else None

    def lease_jobs(
        self,
        worker_id: str,
        capabilities: Iterable[str] = (),
        *,
        max_jobs: int = 1,
        caps: ConcurrencyCaps | None = None,
    ) -> list[LeasedJob]:
        from bajutsu.serve.capabilities import can_serve, checks_routes
        from bajutsu.serve.server.models import JobRecord

        # Sweep dead workers' leases back into the queue before serving, so a stuck job is picked up
        # on the next poll without a separate reaper process.
        self.reclaim_expired_leases(self._lease_timeout, max_attempts=self._max_attempts)
        advertised = set(capabilities)
        # A worker that checks no routes drops the route tokens from every verdict, so jobs that
        # differ in those tokens alone share its verdict and pair by base key (BE-0484).
        pair = JobRecord.route_key if checks_routes(advertised) else JobRecord.base_key
        caps = caps or ConcurrencyCaps()
        # Scopes a count in this call found full: a later group in one stays queued without taking
        # the lock and counting again, which on SQLite would hold the database's write lock per group.
        full: set[tuple[int, str]] = set()
        for candidate in self._lease_candidates(pair):
            if not can_serve(candidate.capabilities or [], advertised):
                continue
            if candidate.group_id is None:
                leased = self._claim_plain(worker_id, candidate.id, advertised)
            else:
                scopes = _capped_scopes(caps, candidate.org_id, candidate.actor)
                if full.intersection(scopes):
                    continue
                leased = self._claim_bundle(
                    worker_id,
                    advertised,
                    group=(candidate.group_id, pair, candidate.pair),
                    scopes=scopes,
                    limit=max(1, max_jobs),
                    caps=caps,
                    full=full,
                )
            if leased:
                return leased
        return []

    def _lease_candidates(self, pair: InstrumentedAttribute[str | None]) -> list[Any]:
        """Every queued plain job, and the oldest queued job of each group and *pair* key, oldest
        first; read with no lock, as each claim re-checks its row.

        One job stands for its pair because the pair key digests every per-job input of
        `can_serve` (BE-0484), so the scan's `can_serve` calls follow the groups, not their size.
        The plain scan stays unbounded: an unroutable backlog piles up at the head, so a fixed
        window would hide a servable younger job (BE-0166); `bajutsu_unroutable_jobs` surfaces it.
        """
        from sqlalchemy import func, literal, null, select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        # Ids are the registry's counter, and one `run-set` shares a `created_at`, so they break ties.
        order = (JobRecord.created_at, func.length(JobRecord.id), JobRecord.id)
        queued = JobRecord.status == "queued"
        columns = (
            JobRecord.id,
            JobRecord.capabilities,
            JobRecord.created_at,
            JobRecord.org_id,
            JobRecord.actor,
        )
        ranked = (
            select(
                *columns,
                JobRecord.group_id,
                pair.label("pair"),
                func.row_number()
                .over(partition_by=(JobRecord.group_id, pair), order_by=order)
                .label("rank"),
            )
            .where(queued, JobRecord.group_id.is_not(None))
            .subquery()
        )
        with Session(self._engine) as session:
            plain = session.execute(
                select(*columns, null().label("group_id"), literal(None).label("pair"))
                .where(queued, JobRecord.group_id.is_(None))
                .order_by(*order)
            ).all()
            grouped = session.execute(
                select(ranked.c.id, ranked.c.capabilities, ranked.c.created_at)
                .add_columns(ranked.c.org_id, ranked.c.actor, ranked.c.group_id, ranked.c.pair)
                .where(ranked.c.rank == 1)
                .order_by(ranked.c.created_at, func.length(ranked.c.id), ranked.c.id)
            ).all()
        # Stable, so a plain job keeps its place ahead of a group registered in the same instant.
        return sorted([*plain, *grouped], key=lambda row: _as_utc(row.created_at))

    def _claim_plain(self, worker_id: str, job_id: str, advertised: set[str]) -> list[LeasedJob]:
        """Lease the plain job *job_id*, or nothing when another worker took it since the scan."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            if self._skip_locked:
                # PostgreSQL takes the row with `FOR UPDATE SKIP LOCKED`, so a row another leaser is
                # mid-claim on is skipped at once instead of waited on — without it every leaser
                # would convoy on the oldest candidate behind one stalled claimant.
                locked = session.execute(
                    select(JobRecord.id)
                    .where(JobRecord.id == job_id, JobRecord.status == "queued")
                    .with_for_update(skip_locked=True)
                ).first()
                if locked is None:
                    return []
            row = session.get(JobRecord, job_id)
            leased = [] if row is None else self._claim_row(session, row, worker_id, advertised)
            session.commit()
            return leased

    def _claim_bundle(
        self,
        worker_id: str,
        advertised: set[str],
        *,
        group: tuple[str, InstrumentedAttribute[str | None], str | None],
        scopes: list[tuple[int, str]],
        limit: int,
        caps: ConcurrencyCaps,
        full: set[tuple[int, str]],
    ) -> list[LeasedJob]:
        """Lease up to *limit* queued jobs of one group and pair key, oldest first, within the
        headroom the caps leave in *scopes* (BE-0484); a scope found full joins *full*.

        The count and the claim share one transaction under a lock taken before the count, so two
        leasers never overshoot a cap together: SQLite's write lock, or PostgreSQL's
        transaction-scoped advisory lock per capped scope. Each lease holds one candidate's locks.
        """
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        group_id, pair, key = group
        with Session(self._engine) as session:
            if scopes:
                self._lock_scopes(session, scopes)
                room = self._headroom(session, caps, scopes)
                full.update(scope for scope, left in room.items() if left <= 0)
                limit = min(limit, *room.values())
                if limit <= 0:
                    session.rollback()
                    return []
            query = (
                select(JobRecord)
                .where(JobRecord.status == "queued", JobRecord.group_id == group_id, pair == key)
                .order_by(JobRecord.created_at, func.length(JobRecord.id), JobRecord.id)
                .limit(limit)
            )
            if self._skip_locked:
                # Workers that chose the same pair spread across its jobs instead of racing for one.
                query = query.with_for_update(skip_locked=True)
            leased = [
                claimed
                for row in session.scalars(query).all()
                for claimed in self._claim_row(session, row, worker_id, advertised)
            ]
            session.commit()
            return leased

    @property
    def _skip_locked(self) -> bool:
        return self._engine.dialect.name != "sqlite"

    def _lock_scopes(self, session: Session, scopes: list[tuple[int, str]]) -> None:
        """Serialize the cap count of every scope in *scopes*, until the transaction ends."""
        from sqlalchemy import func, select

        if not self._skip_locked:
            # Must be the connection's first statement: pysqlite then opens no implicit transaction
            # of its own, so the write lock holds until the commit. `isolation_level=None` would do
            # the same but change every other path on the shared engine.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return
        # One lock per scope, always in the same order, so two leasers cannot deadlock; the two-key
        # form keeps keys of different scopes apart.
        for kind, name in scopes:
            session.execute(select(func.pg_advisory_xact_lock(kind, _lock_key(name))))

    def _headroom(
        self, session: Session, caps: ConcurrencyCaps, scopes: list[tuple[int, str]]
    ) -> dict[tuple[int, str], int]:
        """How many more jobs each capped scope in *scopes* admits now (BE-0484).

        A slot is held by every leased job, and by every queued plain job some live worker can
        serve: the registry admitted it against the caps already, and a worker may take it next. A
        queued plain job no live worker serves holds none, or it would block its org indefinitely.
        """
        from sqlalchemy import or_, select

        from bajutsu.serve.capabilities import can_serve, route_key
        from bajutsu.serve.server.models import JobRecord

        rows = session.execute(
            select(
                JobRecord.status, JobRecord.org_id, JobRecord.actor, JobRecord.capabilities
            ).where(
                or_(
                    JobRecord.status == "leased",
                    (JobRecord.status == "queued") & JobRecord.group_id.is_(None),
                )
            )
        ).all()
        live = self._live_capabilities(session)
        servable: dict[str, bool] = {}
        holding: list[tuple[str, str | None]] = []
        for status, row_org, row_actor, required in rows:
            if status == "queued":
                key = route_key(required or [])
                if key not in servable:
                    servable[key] = any(can_serve(required or [], adv) for adv in live)
                if not servable[key]:
                    continue
            holding.append((row_org, row_actor))
        room: dict[tuple[int, str], int] = {}
        for kind, name in scopes:
            if kind == _DEPLOYMENT_SCOPE:
                room[kind, name] = caps.total - len(holding)
            elif kind == _ORG_SCOPE:
                room[kind, name] = caps.per_org - sum(1 for o, _ in holding if o == name)
            else:
                room[kind, name] = caps.per_user - sum(1 for _, a in holding if a == name)
        return room

    def _claim_row(
        self, session: Session, row: JobRecord, worker_id: str, advertised: set[str]
    ) -> list[LeasedJob]:
        """Move *row* from queued to leased, or nothing when another worker took it first.

        One conditional UPDATE both checks and writes, so two leasers can never both take a row —
        on SQLite, which has no `FOR UPDATE SKIP LOCKED`, as on PostgreSQL (BE-0449).
        """
        from sqlalchemy import update

        from bajutsu.serve.capabilities import UNCHECKED_PROVENANCE, unchecked_lease
        from bajutsu.serve.server.models import JobRecord

        claimed = session.execute(
            update(JobRecord)
            .where(JobRecord.id == row.id, JobRecord.status == "queued")
            .values(status="leased", leased_at=datetime.now(UTC), leased_by=worker_id)
            .execution_options(synchronize_session=False)
        )
        if _rowcount(claimed) != 1:
            return []
        caps = list(row.capabilities or [])
        spec = dict(row.spec)
        # A worker from before BE-0467 serves the job on today's tokens alone, so the job carries
        # that on to its report. Set or cleared on every lease: a job re-queued after such a worker
        # died, then run by a checking one, reports no stale note. Judged by the job's own list,
        # not its bundle anchor's.
        provenance = {
            k: v for k, v in (spec.get("provenance") or {}).items() if k not in UNCHECKED_PROVENANCE
        }
        if unchecked_lease(caps, advertised):
            provenance.update(UNCHECKED_PROVENANCE)
        if provenance != (spec.get("provenance") or {}):
            spec["provenance"] = provenance or None
            row.spec = spec
        return [
            LeasedJob(
                id=row.id,
                org_id=row.org_id,
                spec=spec,
                capabilities=caps,
                group_id=row.group_id,
                compat_key=row.compat_key,
            )
        ]

    def leased_group_counts(self) -> dict[tuple[str, str | None], int]:
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        query = (
            select(JobRecord.org_id, JobRecord.actor, func.count())
            .where(JobRecord.status == "leased", JobRecord.group_id.is_not(None))
            .group_by(JobRecord.org_id, JobRecord.actor)
        )
        with Session(self._engine) as session:
            return {(org, actor): count for org, actor, count in session.execute(query)}

    def _live_capabilities(self, session: Session) -> list[list[str]]:
        from sqlalchemy import select

        from bajutsu.serve.server.models import WorkerRecord

        cutoff = datetime.now(UTC) - self._lease_timeout
        live = session.scalars(
            select(WorkerRecord.capabilities).where(WorkerRecord.last_seen >= cutoff)
        ).all()
        return [list(caps or []) for caps in live]

    def live_worker_capabilities(self) -> list[list[str]]:
        from sqlalchemy.orm import Session

        with Session(self._engine) as session:
            return self._live_capabilities(session)

    def lacks_live_worker(self, job_id: str) -> bool:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.capabilities import can_serve
        from bajutsu.serve.server.models import JobRecord, WorkerRecord

        cutoff = datetime.now(UTC) - self._lease_timeout
        with Session(self._engine) as session:
            # Only the routing key is read, never `spec`, which carries the run's materials.
            row = session.execute(
                select(JobRecord.status, JobRecord.capabilities).where(JobRecord.id == job_id)
            ).first()
            if row is None or row.status != "queued":
                return False
            required = list(row.capabilities or [])
            live = session.scalars(
                select(WorkerRecord.capabilities).where(WorkerRecord.last_seen >= cutoff)
            ).all()
        return not any(can_serve(required, list(caps or [])) for caps in live)

    def live_environments(self) -> dict[str, int]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.common.capability.worker_capability import ENVIRONMENT_ROUTING_PREFIX
        from bajutsu.serve.server.models import WorkerRecord

        cutoff = datetime.now(UTC) - self._lease_timeout
        with Session(self._engine) as session:
            live = session.scalars(
                select(WorkerRecord.capabilities).where(WorkerRecord.last_seen >= cutoff)
            ).all()
        counts: dict[str, int] = {}
        for caps in live:
            # A set, so a worker advertising one name twice still counts once.
            names = {
                cap.removeprefix(ENVIRONMENT_ROUTING_PREFIX)
                for cap in caps or []
                if cap.startswith(ENVIRONMENT_ROUTING_PREFIX)
            }
            for name in names:
                counts[name] = counts.get(name, 0) + 1
        return counts

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
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            # One conditional UPDATE checks ownership and renews in the same statement, so a
            # heartbeat that lands after a reclaim matches no row (the job is `queued` again) and
            # returns False rather than resurrecting `leased_at` on a re-queued job (BE-0449).
            renewed = session.execute(
                update(JobRecord)
                .where(
                    JobRecord.id == job_id,
                    JobRecord.status == "leased",
                    JobRecord.leased_by == worker_id,
                )
                .values(leased_at=datetime.now(UTC))
            )
            session.commit()
            return _rowcount(renewed) == 1

    def holds_lease(self, job_id: str, worker_id: str) -> bool:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            held = session.scalar(
                select(JobRecord.id).where(
                    JobRecord.id == job_id,
                    JobRecord.status == "leased",
                    JobRecord.leased_by == worker_id,
                )
            )
            return held is not None

    def cancel_requested(self, job_id: str, worker_id: str) -> bool:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            flag = session.scalar(
                select(JobRecord.cancel_requested).where(
                    JobRecord.id == job_id,
                    JobRecord.status == "leased",
                    JobRecord.leased_by == worker_id,
                )
            )
            return bool(flag)

    def open_handoff(self, job_id: str, worker_id: str) -> bool:
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            # An answer left from an earlier request is dropped, so the worker takes only an answer
            # to the request it just made. A request still waiting on a person keeps the handoff:
            # one at a time, so a looping worker cannot stack prompts on the page.
            opened = session.execute(
                update(JobRecord)
                .where(
                    JobRecord.id == job_id,
                    JobRecord.status == "leased",
                    JobRecord.leased_by == worker_id,
                    JobRecord.handoff_pending.is_(False),
                )
                .values(handoff_pending=True, handoff_response=None)
            )
            session.commit()
            return _rowcount(opened) == 1

    def answer_handoff(self, job_id: str, token: str) -> bool:
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            answered = session.execute(
                update(JobRecord)
                .where(
                    JobRecord.id == job_id,
                    JobRecord.status == "leased",
                    JobRecord.handoff_pending.is_(True),
                    JobRecord.cancel_requested.is_(False),
                )
                .values(handoff_pending=False, handoff_response=token)
            )
            session.commit()
            return _rowcount(answered) == 1

    def take_handoff_response(self, job_id: str, worker_id: str) -> str | None:
        from sqlalchemy import select, update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        owned = (
            JobRecord.id == job_id,
            JobRecord.status == "leased",
            JobRecord.leased_by == worker_id,
        )
        with Session(self._engine) as session:
            token = session.scalar(select(JobRecord.handoff_response).where(*owned))
            if token is None:
                return None
            # Cleared on the value read, so two polls racing for one answer cannot both take it.
            taken = session.execute(
                update(JobRecord)
                .where(*owned, JobRecord.handoff_response == token)
                .values(handoff_response=None)
            )
            session.commit()
            return token if _rowcount(taken) == 1 else None

    def reclaim_expired_leases(
        self, timeout: timedelta, *, max_attempts: int = DEFAULT_LEASE_MAX_ATTEMPTS
    ) -> list[str]:
        from sqlalchemy import delete, select, update
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
            scan = (
                select(JobRecord.id, JobRecord.attempts, JobRecord.cancel_requested)
                .where(JobRecord.status == "leased", JobRecord.leased_at < cutoff)
                # One lock order for every reclaimer, so two sweeps running at once (each lease
                # poll runs one) never deadlock on PostgreSQL.
                .order_by(JobRecord.id)
            )
            if self._engine.dialect.name != "sqlite":
                # Skip a row a heartbeat (or another sweep) holds rather than wait on it: that
                # worker is alive, and since every lease poll runs this sweep, waiting would let
                # one delayed heartbeat stall queue service for every worker.
                scan = scan.with_for_update(skip_locked=True)
            expired = session.execute(scan).all()
            for job_id, previous_attempts, cancelling in expired:
                attempts = previous_attempts + 1
                exhausted = attempts >= max_attempts
                # A job a cancel already reached is not run again when its worker dies: it ends
                # `cancelled`, the outcome its requester asked for (BE-0449).
                outcome = _reclaimed_outcome(
                    job_id, attempts, exhausted=exhausted, cancelling=cancelling
                )
                # Re-checked in the UPDATE itself: a heartbeat that renewed the lease since the scan
                # moved `leased_at` past the cutoff, so that live worker's job matches no row and is
                # left alone rather than reclaimed out from under it (BE-0449). `attempts` must still
                # read what the scan saw, so a count from before a concurrent reclaim never
                # overwrites the newer one, whatever the replicas' clocks say. The cancel flag must
                # too, so a cancel landing after the scan is never re-queued with the flag still set.
                reclaimed = session.execute(
                    update(JobRecord)
                    .where(
                        JobRecord.id == job_id,
                        JobRecord.status == "leased",
                        JobRecord.leased_at < cutoff,
                        JobRecord.attempts == previous_attempts,
                        JobRecord.cancel_requested == cancelling,
                    )
                    .values(
                        attempts=attempts,
                        leased_by=None,
                        leased_at=None,
                        # The paused `record` died with the lease, so its handoff ends too.
                        handoff_pending=False,
                        handoff_response=None,
                        **outcome,
                    )
                )
                if _rowcount(reclaimed) == 1 and outcome["status"] == "queued":
                    requeued.append(job_id)
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
        overwritten. One conditional UPDATE checks and writes, so it is atomic against a reclaim on
        every dialect (BE-0449)."""
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        conditions = [JobRecord.id == job_id, JobRecord.status == "leased"]
        if worker_id is not None:
            conditions.append(JobRecord.leased_by == worker_id)
        with Session(self._engine) as session:
            finished = session.execute(
                update(JobRecord)
                .where(*conditions)
                # A finished job keeps no handoff answer: it may hold a one-time password.
                .values(status=status, result=payload, handoff_pending=False, handoff_response=None)
            )
            session.commit()
            return _rowcount(finished) == 1

    def cancel_job(self, job_id: str) -> tuple[str, bool] | None:
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            # Each step is one conditional UPDATE, like every other transition (BE-0449). A row can
            # move between them — leased by a worker after the first missed, or reclaimed back to
            # the queue after the second — so the pair is retried a bounded number of times; a
            # terminal status ends it at once.
            status = "queued"
            for _ in range(_CANCEL_ATTEMPTS):
                queued = session.execute(
                    update(JobRecord)
                    .where(JobRecord.id == job_id, JobRecord.status == "queued")
                    .values(status="cancelled", result=cancelled_job_result(job_id))
                )
                if _rowcount(queued) == 1:
                    session.commit()
                    return "cancelled", True
                leased = session.execute(
                    update(JobRecord)
                    .where(JobRecord.id == job_id, JobRecord.status == "leased")
                    .values(cancel_requested=True)
                )
                session.commit()
                if _rowcount(leased) == 1:
                    return "leased", True
                row = session.get(JobRecord, job_id)
                if row is None:
                    return None
                status = row.status
                if status in TERMINAL_JOB_STATUSES:
                    return status, False
                session.expire_all()
            return status, False

    def cancel_leased_job(self, job_id: str, result: dict[str, Any], *, worker_id: str) -> bool:
        return self._finish_job(job_id, status="cancelled", payload=result, worker_id=worker_id)

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
                "spec": dict(row.spec),
                "awaiting_human": bool(row.handoff_pending),
            }

    def finished_job_ids(self, job_ids: Iterable[str]) -> set[str]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        ids = list(job_ids)
        if not ids:
            return set()
        stmt = select(JobRecord.id).where(
            JobRecord.id.in_(ids), JobRecord.status.in_(TERMINAL_JOB_STATUSES)
        )
        with Session(self._engine) as session:
            return set(session.scalars(stmt))

    def save_batch_run_arn(self, job_id: str, run_arn: str, *, worker_id: str) -> bool:
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import JobRecord

        with Session(self._engine) as session:
            # One conditional UPDATE, as `heartbeat_job` does: a save landing after a reclaim matches
            # no row, so a stale worker never overwrites the run the job's next attempt resumes.
            saved = session.execute(
                update(JobRecord)
                .where(
                    JobRecord.id == job_id,
                    JobRecord.status == "leased",
                    JobRecord.leased_by == worker_id,
                )
                .values(batch_state={"run_arn": run_arn})
            )
            session.commit()
            return _rowcount(saved) == 1

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

        from bajutsu.serve.capabilities import can_serve, route_key
        from bajutsu.serve.server.models import JobRecord, WorkerRecord

        now = datetime.now(UTC)
        queued: dict[str, int] = defaultdict(int)
        leased: dict[str, int] = defaultdict(int)
        # Per worker, keep its freshest lease renewal (max leased_at) — that is its last heartbeat.
        latest_heartbeat: dict[str, datetime] = {}
        oldest_in_flight = 0.0
        # Queued jobs by route key: one `can_serve` verdict per key, weighted by its job count, so a
        # scrape costs per key rather than per scenario of a large group (BE-0484).
        queued_caps: dict[str, list[str]] = {}
        queued_weight: dict[str, int] = defaultdict(int)
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
            JobRecord.route_key,
        ).where(JobRecord.status.in_(("queued", "leased")))
        cutoff = now - self._lease_timeout
        with Session(self._engine) as session:
            for status, org_id, leased_by, leased_at, created_at, caps, key in session.execute(
                stmt
            ):
                if status == "queued":
                    queued[org_id] += 1
                    pair = key or route_key(caps or [])
                    queued_caps.setdefault(pair, list(caps or []))
                    queued_weight[pair] += 1
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
            # worker advertises all of its required capabilities — the same `can_serve` test (a subset
            # test, with an `anyof:` group met by any one member) the lease filter uses, so
            # "unroutable" means exactly "no worker would lease it".
            live = [
                list(w.capabilities or [])
                for w in session.scalars(
                    select(WorkerRecord).where(WorkerRecord.last_seen >= cutoff)
                )
            ]
        unroutable = sum(
            weight
            for key, weight in queued_weight.items()
            if not any(can_serve(queued_caps[key], adv) for adv in live)
        )
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


# The advisory-lock namespaces of the three capped scopes, in the order a lease takes them.
_DEPLOYMENT_SCOPE, _ORG_SCOPE, _USER_SCOPE = 1, 2, 3


def _capped_scopes(caps: ConcurrencyCaps, org_id: str, actor: str | None) -> list[tuple[int, str]]:
    """The scopes whose cap binds a job of *org_id* and *actor*, in lock order."""
    scopes: list[tuple[int, str]] = []
    if caps.total > 0:
        scopes.append((_DEPLOYMENT_SCOPE, ""))
    if caps.per_org > 0:
        scopes.append((_ORG_SCOPE, org_id))
    if caps.per_user > 0 and actor:
        scopes.append((_USER_SCOPE, actor))
    return scopes


def _lock_key(name: str) -> int:
    """*name* as a signed 32-bit advisory-lock key; two names that collide merely serialize."""
    return (zlib.crc32(name.encode()) ^ 0x80000000) - 0x80000000

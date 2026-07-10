"""The `Repository` seam: the hosted backend's system of record (BE-0015 7a).

Shaped like the other server seams (`object_store.py`): a `Protocol`, a SQLAlchemy implementation,
and an env-driven factory — with SQLAlchemy and the ORM models lazy-imported inside the functions
that need them, so the default `serve`/CLI path never loads them (the import guard locks this).
7a implements only the `runs` methods; `RunRecord` is the boundary type so ORM rows never leak
past the seam."""

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

    from bajutsu.serve.server.models import Run

# A lease with no heartbeat for this long is treated as a dead worker and reclaimed; a job that is
# reclaimed this many times is failed rather than re-queued forever (BE-0016 worker liveness). The
# worker's heartbeat interval must stay well under the timeout so a live long run is never reclaimed.
DEFAULT_LEASE_TIMEOUT_SECONDS = 120.0
DEFAULT_LEASE_MAX_ATTEMPTS = 3


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


@runtime_checkable
class Repository(Protocol):
    """Persistence for the control plane. 7a covers runs; identity/audit land in 7b/7c."""

    def record_run(self, run: RunRecord) -> None:
        """Insert *run*, or update it in place when its id already exists (e.g. a status change)."""

    def get_run(self, run_id: str) -> RunRecord | None:
        """The run with *run_id*, or None if there is none."""

    def list_runs(self, *, org_id: str, limit: int = 50) -> list[RunRecord]:
        """An org's runs, newest first, capped at *limit*."""

    def ensure_org(self, org_id: str, *, slug: str, name: str) -> None:
        """Create the org if it does not exist yet (idempotent) — 7c-1's single default org."""

    def upsert_user(
        self, user_id: str, *, org_id: str, github_login: str, email: str, role: str = "editor"
    ) -> None:
        """Insert the user, or update it in place when its id already exists (an OAuth re-login),
        setting its *role* (recomputed from policy each login, BE-0015 7c-2)."""

    def user_role(self, user_id: str) -> str | None:
        """The user's role (viewer/editor/admin), or None if there is no such user."""

    def user_org(self, user_id: str) -> str | None:
        """The user's org id, or None if there is no such user (BE-0015 multi-tenancy)."""

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

    def list_runs(self, *, org_id: str, limit: int = 50) -> list[RunRecord]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Run

        stmt = select(Run).where(Run.org_id == org_id).order_by(Run.created_at.desc()).limit(limit)
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
                    return
                except IntegrityError:
                    # A concurrent OAuth callback inserted the same user first; fall through to
                    # update the now-existing row instead of failing the login.
                    session.rollback()
                    user = session.get(User, user_id)
            if user is not None:  # update in place (a re-login) without disturbing created_at
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
                    return
                except IntegrityError:
                    session.rollback()
                    row = session.get(WorkerRecord, worker_id)
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

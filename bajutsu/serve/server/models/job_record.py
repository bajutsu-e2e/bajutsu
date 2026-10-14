"""The jobs table: one row per job, with its lease and its outcome."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, false
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime, Text

from ._functions import _created_at
from ._shared import _JSON
from .base import Base


class JobRecord(Base):
    __tablename__ = "jobs"
    # The lease/reclaim hot paths filter on status (and leased_at for reclaim), swept on every poll,
    # so these composite indexes keep them off a full-table scan as the jobs table grows. The second
    # (status, created_at) serves the capability-aware lease scan (BE-0166), which reads queued rows
    # `ORDER BY created_at` — the index provides both the filter and the order.
    __table_args__ = (
        Index("ix_jobs_status_leased_at", "status", "leased_at"),
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(default="")
    spec: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)
    # Capability tokens a worker must advertise to lease this job (BE-0166): its platform axis plus
    # the target's `requires`. The routing key lives on the row (not a new store); the lease filter
    # serves a job only to a worker whose advertised set is a superset. Empty = any worker (a job
    # with no declared requirement, e.g. triage), preserving the pre-routing single-queue behavior.
    capabilities: Mapped[list[str]] = mapped_column(_JSON, default=list)
    # queued | leased | done | failed | cancelled
    status: Mapped[str] = mapped_column(default="queued")
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    leased_by: Mapped[str | None] = mapped_column(default=None)
    # How many times this job has been leased and lost (lease expiry) — a poison job that keeps
    # killing its worker is failed once it hits the attempt cap rather than re-queued forever.
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    # A cancel that reached a job already leased to a worker (BE-0449). The row stays ``leased`` so
    # the worker keeps its lease while it stops the run; the heartbeat response carries the flag, and
    # only the worker's cancelled result moves the row to ``cancelled``.
    cancel_requested: Mapped[bool] = mapped_column(default=False, server_default=false())
    # A `record` on a worker paused for a person (BE-0449 unit 8): set when the worker posts the
    # handoff request, cleared when the person answers. The answer waits in `handoff_response` as a
    # Fernet token under the secrets key, since it may carry a value such as a one-time password,
    # until the worker takes it; any replica can then hand it over.
    handoff_pending: Mapped[bool] = mapped_column(default=False, server_default=false())
    handoff_response: Mapped[str | None] = mapped_column(Text, default=None)
    result: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)
    # The scheduled cloud-batch (Device Farm) run's ARN, persisted once the run is scheduled so a
    # worker that re-leases this job after a restart resumes polling that run instead of resubmitting
    # it (BE-0336 Unit 5). Empty ``{}`` until the job is a scheduled cloud-batch run.
    batch_state: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)
    created_at: Mapped[datetime] = _created_at()

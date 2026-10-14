"""The runs table: one row per run the control plane recorded."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from ._functions import _created_at
from ._shared import _JSON
from .base import Base


class Run(Base):
    __tablename__ = "runs"
    # scenario_hash is half the flakiness grouping key: the DB-level score groups by it together
    # with the run's device OS (BE-0358), at the run level, as documented in
    # bajutsu/serve/flakiness.py — the coarser counterpart to audit --history's
    # per-(fingerprint, scenario, OS) grouping.
    __table_args__ = (Index("ix_runs_scenario_hash", "scenario_hash"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), default=None)
    status: Mapped[str] = mapped_column(default="")
    ok: Mapped[bool | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = _created_at()
    summary: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict)
    # Run provenance mirrored from the run's manifest.json (BE-0049 stamp), so cross-run flakiness
    # can group by scenario identity straight from the DB (BE-0220) without re-reading every
    # manifest from object storage. Null for a pre-provenance run — unstamped, so ungroupable.
    scenario_hash: Mapped[str | None] = mapped_column(default=None)
    tool_version: Mapped[str | None] = mapped_column(default=None)
    git_revision: Mapped[str | None] = mapped_column(default=None)
    # The device OS the run happened on, mirrored from the manifest's per-scenario `device_runtime`
    # so the flakiness score groups per OS version straight from the DB (BE-0358). Null means "never
    # determined" — a row recorded before this column existed — as distinct from the empty string,
    # which records that the run named no single OS; see `db.RunRecord`.
    device_runtime: Mapped[str | None] = mapped_column(default=None)
    # The run-history partition, resolved at enqueue and carried on the `Job` (BE-0404 unit 2):
    # the label the launcher derives from the bound config, or the operator's `--label` override.
    # Opaque — never parsed, never matched against config, never consulted by authorization. Null
    # for a run recorded before the column existed, which unit 4's empty-match fallback keeps visible.
    label: Mapped[str | None] = mapped_column(default=None)
    # The target this run ran, mirrored from the manifest like `scenario_hash` and `device_runtime`
    # (BE-0404 unit 3), so "Android passes while iOS fails" is computable from stored data. A column
    # of its own rather than a reserved `label` value: a target name is config-declared and carries
    # authorization weight (`orgs.<name>.targets`), while a label is untrusted operator free-text.
    target: Mapped[str | None] = mapped_column(default=None)
    # The run's coordinates when `runsOn` conditions formed its runs (BE-0452), mirrored from the
    # manifest like `device_runtime`, for the flakiness grouping key; see `db.RunRecord`.
    run_coordinates: Mapped[str | None] = mapped_column(default=None)
    # Soft-delete (BE-0239): a run with `deleted_at` set is trashed — hidden from `list_runs` but
    # restorable within the retention window; `deleted_by` records the user id who did it, for the
    # audit reach. Null for a live run. `deleted_by` is a plain column, not an FK to users.id like
    # `created_by`: it is added after the initial migration, and the SQLite gate cannot ALTER-ADD an
    # FK column (migration 0008 set the same precedent for later columns). The referential reach that
    # matters lives in audit_log, whose `target` records the run id and `actor_id` the user.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    deleted_by: Mapped[str | None] = mapped_column(default=None)

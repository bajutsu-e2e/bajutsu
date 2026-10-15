"""A run as the persistence seam exchanges it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RunRecord:
    """A run as the seam exchanges it — the relational core plus the JSON manifest summary."""

    id: str
    org_id: str
    status: str
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
    # The run-history partition, resolved at enqueue and carried on the `Job` (BE-0404 unit 2) —
    # the launcher's derived label or the operator's `--label`. None for a run recorded before the
    # column existed, or one enqueued with no bound config to derive a label from.
    label: str | None = None
    # The target this run ran, mirrored from the manifest like `device_runtime` (BE-0404 unit 3), so
    # a cross-target comparison reads it from the DB. None when the run named no target.
    target: str | None = None
    # The coordinates every scenario of the run shared, as `devices.runs.coordinates_key` spells them
    # (BE-0452): with `target` and `device_runtime`, the flakiness grouping key, so a failure on one
    # model never reads as flakiness. `""` for a run no condition formed, `*` for one whose results
    # ran on different coordinates; None for a row recorded before the column existed, which the
    # flakiness score reads as `""` because no such run could carry a condition.
    run_coordinates: str | None = None
    # Soft-delete marker (BE-0239): when set, the run is trashed — hidden from `list_runs` unless
    # `include_deleted`. None for a live run. `record_run` never writes these (a status update must
    # not resurrect or re-trash a run); only `soft_delete_run`/`restore_run` touch them.
    deleted_at: datetime | None = None
    deleted_by: str | None = None

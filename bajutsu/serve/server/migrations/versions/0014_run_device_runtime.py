"""add runs.device_runtime, the flakiness grouping key's OS component (BE-0358)

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mirror the run's device OS label onto the DB record so cross-run flakiness groups per OS
    # version straight from the DB (BE-0358) — `_run_summary` drops it at that seam, so without this
    # column a fleet's genuine OS differences keep scoring as flakiness. Nullable, and null means
    # "never determined": an already-recorded run keeps it until the hosted panel backfills the value
    # from that run's stored manifest, which is where the per-scenario label already sits. No index —
    # the grouping runs in Python over a bounded window of already-fetched rows.
    op.add_column("runs", sa.Column("device_runtime", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "device_runtime")

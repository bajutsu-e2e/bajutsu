"""add attempts column to jobs for lease re-queue (BE-0016 worker liveness)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    # Keep the per-poll lease/reclaim filters (status, and leased_at for reclaim) off a full scan.
    op.create_index("ix_jobs_status_leased_at", "jobs", ["status", "leased_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status_leased_at", table_name="jobs")
    op.drop_column("jobs", "attempts")

"""add job_groups and the jobs columns that place a job in one (BE-0484 unit 1)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_COLUMNS = ("actor", "group_id", "compat_key", "route_key", "base_key")


def upgrade() -> None:
    op.create_table(
        "job_groups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # NULL on existing rows: no job belonged to a group before this, and none recorded its actor.
    for name in _JOB_COLUMNS:
        op.add_column("jobs", sa.Column(name, sa.String(), nullable=True))
    op.create_index("ix_jobs_group_id", "jobs", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_group_id", table_name="jobs")
    # `batch_alter_table` because SQLite has no in-place DROP, as in 0022's downgrade.
    with op.batch_alter_table("jobs") as batch:
        for name in reversed(_JOB_COLUMNS):
            batch.drop_column(name)
    op.drop_table("job_groups")

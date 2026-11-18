"""key a job group by its org and request, and index the grouped lease scan (BE-0484 unit 3)

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCAN_INDEXES = {
    "ix_jobs_status_group_route": ["status", "group_id", "route_key", "created_at"],
    "ix_jobs_status_group_base": ["status", "group_id", "base_key", "created_at"],
}


def upgrade() -> None:
    # NULL on a group written before this: its key was server-generated, so no request repeats it.
    op.add_column("job_groups", sa.Column("digest", sa.String(), nullable=True))
    op.create_index("ux_job_groups_org_key", "job_groups", ["org_id", "key"], unique=True)
    for name, columns in _SCAN_INDEXES.items():
        op.create_index(name, "jobs", columns)


def downgrade() -> None:
    for name in reversed(_SCAN_INDEXES):
        op.drop_index(name, table_name="jobs")
    op.drop_index("ux_job_groups_org_key", table_name="job_groups")
    # `batch_alter_table` because SQLite has no in-place DROP, as in 0025's downgrade.
    with op.batch_alter_table("job_groups") as batch:
        batch.drop_column("digest")

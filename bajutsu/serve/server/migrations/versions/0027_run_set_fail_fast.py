"""add a job group's fail-fast flag and a job's cancel reason (BE-0499)

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_groups",
        sa.Column("fail_fast", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("jobs", sa.Column("cancel_reason", sa.String(), nullable=True))


def downgrade() -> None:
    # `batch_alter_table` because SQLite has no in-place DROP, as in 0026's downgrade.
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("cancel_reason")
    with op.batch_alter_table("job_groups") as batch:
        batch.drop_column("fail_fast")

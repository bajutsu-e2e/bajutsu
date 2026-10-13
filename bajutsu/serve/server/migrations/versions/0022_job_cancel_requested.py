"""add jobs.cancel_requested so a cancel reaches a job a worker has leased (BE-0449 unit 2)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # False on existing rows: no cancel could reach a leased job before this column existed.
    op.add_column(
        "jobs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # Before 0022 no status read `cancelled`, so a cancelled job goes back as the terminal state the
    # older code knows; left as is, it would sit outside every status that code filters on.
    op.execute("UPDATE jobs SET status = 'failed' WHERE status = 'cancelled'")
    # `batch_alter_table` because SQLite has no in-place DROP, as in 0021's downgrade.
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("cancel_requested")

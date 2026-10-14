"""add jobs.handoff_pending and jobs.handoff_response for the record handoff relay (BE-0449 unit 8)

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # False and empty on existing rows: no handoff could reach a leased job before these existed.
    op.add_column(
        "jobs",
        sa.Column("handoff_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("jobs", sa.Column("handoff_response", sa.Text(), nullable=True))


def downgrade() -> None:
    # `batch_alter_table` because SQLite has no in-place DROP, as in 0022's downgrade.
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("handoff_response")
        batch.drop_column("handoff_pending")

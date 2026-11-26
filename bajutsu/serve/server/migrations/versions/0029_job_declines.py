"""add the columns a declined job carries (BE-0511)

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("decline_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("jobs", sa.Column("first_declined_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("decline_reason", sa.Text()))
    op.add_column("jobs", sa.Column("not_before", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("declined_environment", sa.String()))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        for column in (
            "declined_environment",
            "not_before",
            "decline_reason",
            "first_declined_at",
            "decline_count",
        ):
            batch.drop_column(column)

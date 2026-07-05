"""add secrets table for the write-once operator secret store (BE-0136)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), primary_key=True),
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("ciphertext", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("secrets")

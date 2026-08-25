"""record every org a user may act as, and whether they picked the active one

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every org a login's GitHub memberships admit it to, with the role that pair resolves to.
    # Written fresh on each sign-in, which is the only moment those memberships are known, so
    # nothing here needs backfilling: a user who has not signed in since this migration simply has
    # no rows, and `users.org_id` still names their active org exactly as before.
    op.create_table(
        "user_orgs",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), primary_key=True),
        sa.Column("role", sa.String(), nullable=False),
    )
    # Null for every existing row, which is the honest reading: no user has chosen an active org
    # yet, so sign-in keeps re-resolving `org_id` for all of them exactly as it does today.
    op.add_column("users", sa.Column("org_selected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "org_selected_at")
    op.drop_table("user_orgs")

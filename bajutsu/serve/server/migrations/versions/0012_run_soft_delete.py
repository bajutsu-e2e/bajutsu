"""add soft-delete markers to the runs record: deleted_at / deleted_by (BE-0239)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A trashed run keeps its row but drops out of `list_runs` until restored or purged (BE-0239).
    # Both nullable: an already-recorded (live) run stays null and is simply not trashed, never
    # blocking. `deleted_by` is a plain column (the user id), not an FK — the SQLite gate cannot
    # ALTER-ADD an FK column, and migration 0008 set the same precedent for post-initial columns.
    op.add_column("runs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("deleted_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "deleted_by")
    op.drop_column("runs", "deleted_at")

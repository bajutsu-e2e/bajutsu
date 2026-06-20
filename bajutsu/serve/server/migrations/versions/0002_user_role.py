"""add users.role for RBAC

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite supports ADD COLUMN with a constant server_default directly (no table rebuild), and so
    # do Postgres/etc. Backfill existing (allowlisted) users to 'editor' to match the role policy's
    # default — so 7c-1 users keep run access without needing to re-login (BE-0015 7c-2).
    op.add_column("users", sa.Column("role", sa.String(), nullable=False, server_default="editor"))


def downgrade() -> None:
    op.drop_column("users", "role")

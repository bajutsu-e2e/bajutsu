"""add the sign-in's GitHub facts and the acting org/role to sessions

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, portable JSON on the SQLite gate — the same variant `models._JSON` picks.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # Every column is nullable, so a session issued before this migration keeps working: it records
    # no GitHub facts and no selection, which resolves the org from the user row exactly as before.
    op.add_column("sessions", sa.Column("github_orgs", _JSON, nullable=True))
    op.add_column("sessions", sa.Column("teams", _JSON, nullable=True))
    op.add_column("sessions", sa.Column("org", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("role", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "role")
    op.drop_column("sessions", "org")
    op.drop_column("sessions", "teams")
    op.drop_column("sessions", "github_orgs")

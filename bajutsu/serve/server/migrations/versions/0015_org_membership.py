"""add org membership, its seeded marker, and a soft-delete marker to orgs (BE-0375)

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, portable JSON on the SQLite gate — the same variant `models._JSON` picks.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # Columns only, no seeding: a bound config's `orgs:` block is not reachable from Alembic's
    # migration environment (it resolves BAJUTSU_DATABASE_URL and nothing else), so `serve` seeds
    # each row itself at startup and at every config rebind (BE-0375). Every column is nullable, so
    # an existing row upgrades without a value and reads as "no membership, not yet seeded, live".
    op.add_column("orgs", sa.Column("members", _JSON, nullable=True))
    op.add_column("orgs", sa.Column("github_orgs", _JSON, nullable=True))
    op.add_column("orgs", sa.Column("editor_team", sa.String(), nullable=True))
    op.add_column(
        "orgs", sa.Column("membership_seeded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("orgs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "deleted_at")
    op.drop_column("orgs", "membership_seeded_at")
    op.drop_column("orgs", "editor_team")
    op.drop_column("orgs", "github_orgs")
    op.drop_column("orgs", "members")

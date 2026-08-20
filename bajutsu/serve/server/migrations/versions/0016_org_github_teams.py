"""add the org's admitting GitHub Teams to orgs (BE-XXXX)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-20
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
    # Nullable and unseeded, like the membership columns 0015 added: an existing row upgrades without
    # a value and reads as "this org admits no Team of its own", which is what every org meant before
    # this column existed. `editor_team` keeps its own column — it decides a role as well as
    # admitting, so folding the two would lose which Team may write.
    op.add_column("orgs", sa.Column("github_teams", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "github_teams")

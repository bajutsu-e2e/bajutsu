"""add runs.run_coordinates, the flakiness grouping key's coordinates component (BE-0452)

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mirror the coordinates a run's `runsOn` conditions formed onto the DB record, so the flakiness
    # score keys a run by them beside its target and OS straight from the DB (BE-0452). Nullable, and
    # null on every row recorded before. No such run could carry a condition, so the flakiness score
    # reads null as the empty key and those histories continue rather than being backfilled. No index — the grouping runs in Python over a
    # bounded window of already-fetched rows.
    op.add_column("runs", sa.Column("run_coordinates", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "run_coordinates")

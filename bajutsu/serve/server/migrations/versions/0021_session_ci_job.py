"""carry the CI job a machine session was minted for, so its audit entries name the job

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, portable JSON on the SQLite gate — the same variant `models._JSON` picks.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # Null on existing rows and no backfill runs: the job's claims existed only in the token the
    # session was exchanged for, so a live machine session simply audits without one until it ends.
    op.add_column("sessions", sa.Column("ci_job", _JSON, nullable=True))


def downgrade() -> None:
    # `batch_alter_table` because SQLite has no in-place DROP, as in 0020's downgrade.
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("ci_job")

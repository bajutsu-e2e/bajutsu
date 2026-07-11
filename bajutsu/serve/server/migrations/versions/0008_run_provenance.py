"""add run provenance to the runs record: scenario_hash / tool_version / git_revision (BE-0220)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mirror the run's manifest.json provenance stamp (BE-0049) onto the DB record so cross-run
    # flakiness groups by scenario identity straight from the DB (BE-0220). Nullable: an
    # already-recorded (pre-provenance) run stays null and is simply ungroupable, never blocking.
    op.add_column("runs", sa.Column("scenario_hash", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("tool_version", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("git_revision", sa.String(), nullable=True))
    # The grouping key for the DB-level flakiness score (groups by scenario_hash at the run level).
    op.create_index("ix_runs_scenario_hash", "runs", ["scenario_hash"])


def downgrade() -> None:
    op.drop_index("ix_runs_scenario_hash", table_name="runs")
    op.drop_column("runs", "git_revision")
    op.drop_column("runs", "tool_version")
    op.drop_column("runs", "scenario_hash")

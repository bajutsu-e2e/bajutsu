"""add a job group's notify selection and the group_events table (BE-0500)

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("job_groups", sa.Column("notify", _JSON, nullable=True))
    op.create_table(
        "group_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("sink", sa.String(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("snapshot", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_group_events_group_sink_event",
        "group_events",
        ["group_id", "sink", "event"],
        unique=True,
    )
    op.create_index("ix_group_events_state_next", "group_events", ["state", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_group_events_state_next", table_name="group_events")
    op.drop_index("ux_group_events_group_sink_event", table_name="group_events")
    op.drop_table("group_events")
    # `batch_alter_table` because SQLite has no in-place DROP, as in 0027's downgrade.
    with op.batch_alter_table("job_groups") as batch:
        batch.drop_column("notify")

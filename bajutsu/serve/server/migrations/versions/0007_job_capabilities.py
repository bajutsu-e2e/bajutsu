"""add capability routing: jobs.capabilities column + workers registry (BE-0166)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # A routing key on the existing jobs row (not a new store). Default `[]` so any already-queued
    # job stays leasable by any worker (empty required set), preserving the pre-routing behavior.
    op.add_column(
        "jobs",
        sa.Column("capabilities", _JSON, nullable=False, server_default=sa.text("'[]'")),
    )
    # The live-worker registry: what the pool advertises, refreshed each lease poll — the read the
    # control plane needs to surface an unroutable queued job (no worker can serve it).
    op.create_table(
        "workers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("capabilities", _JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("workers")
    op.drop_column("jobs", "capabilities")

"""add jobs.batch_state for durable cloud-batch poll resume (BE-0336 Unit 5)

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # The scheduled Device Farm run's ARN lives on the existing jobs row (not a new store). Default
    # `{}` so an already-queued job carries no run ARN until it is scheduled — a worker that re-leases
    # it after a restart resumes that run rather than resubmitting (BE-0336 Unit 5).
    op.add_column(
        "jobs",
        sa.Column("batch_state", _JSON, nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("jobs", "batch_state")

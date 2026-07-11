"""add the config-source binding to the projects record (BE-0225)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres (production); the portable JSON type on SQLite (the gate) — matches models.py.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # The config source a project binds, as a discriminated record (`kind` + its `locator`), so the
    # hub records which config each project points at (BE-0225). Nullable: BE-0015's unwired
    # `projects` scaffolding predates the binding, so an existing row stays null rather than blocking.
    op.add_column("projects", sa.Column("source", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "source")

"""add the per-org AI provider settings table (BE-0229)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres (production); the portable JSON type on SQLite (the gate) — matches models.py.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # The per-org AI provider selection (BE-0229): the active provider plus its per-provider
    # model/effort/region slot map, so a hosted deployment resolves provider/model/effort per
    # organization and a saved choice survives a restart. Not sensitive (read back for editing), so
    # stored in the clear — the readable counterpart to the encrypted `secrets` table.
    op.create_table(
        "provider_settings",
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False, server_default=""),
        sa.Column("settings", _JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("provider_settings")

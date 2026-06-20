"""initial system-of-record schema

Revision ID: 0001
Revises:
Create Date: 2026-06-21
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, portable JSON elsewhere (SQLite on the gate) — must match models.py.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def _created_at(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "orgs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        _created_at("created_at"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("github_login", sa.String(), nullable=True),
        _created_at("created_at"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        _created_at("created_at"),
        sa.UniqueConstraint("org_id", "name"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=True),
        _created_at("created_at"),
        sa.Column("summary", _JSON, nullable=False),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        _created_at("at"),
        sa.Column("detail", _JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("runs")
    op.drop_table("projects")
    op.drop_table("users")
    op.drop_table("orgs")

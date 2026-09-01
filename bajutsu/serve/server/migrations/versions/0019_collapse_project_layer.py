"""collapse the project layer into the org and the target (BE-0404)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres (production); the portable JSON type on SQLite (the gate) — matches models.py.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # The org's own config-source record, replacing the project row a hosted replica read to recover
    # an uploaded bundle it did not receive (BE-0404 unit 1). Null until the org binds something.
    op.add_column("orgs", sa.Column("config_source", _JSON, nullable=True))
    # The run-history partition the project layer used to provide, as a plain column (unit 2), and
    # the target axis a cross-target comparison needs (unit 3). Both null on existing rows: no
    # backfill runs, because no deployment holds project-partitioned history worth carrying over,
    # and unit 4's empty-match fallback opens an unlabeled history unfiltered rather than empty.
    op.add_column("runs", sa.Column("label", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("target", sa.String(), nullable=True))
    # `batch_alter_table` because this is the first migration to drop a column on the *upgrade* path:
    # SQLite has no in-place DROP of an FK-bearing column, so alembic rebuilds the table. Postgres
    # takes the plain ALTER underneath the same call.
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("project_id")
    op.drop_table("projects")


def downgrade() -> None:
    # Recreates the table's shape, not its rows — the data is gone with the upgrade, which is the
    # accepted trade (nothing was using the layer). Mirrors 0001's definition plus 0009's `source`.
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "name"),
    )
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("project_id", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_runs_project_id_projects", "projects", ["project_id"], ["id"], ondelete="SET NULL"
        )
    op.drop_column("runs", "target")
    op.drop_column("runs", "label")
    op.drop_column("orgs", "config_source")

"""add ON DELETE SET NULL to runs.project_id FK (BE-0225)

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The original FK (0001_initial) had no ondelete= action, so deleting a project with run
    # history would raise IntegrityError on Postgres. Adding ON DELETE SET NULL lets deregister
    # retain the run rows while clearing their project association — matching the documented
    # contract ("only the binding is removed; run history is retained", BE-0225).
    #
    # SQLite: FKs aren't enforced at runtime (no PRAGMA foreign_keys=ON on the gate) and the
    # dialect doesn't store named FK constraints, so the batch operation is a no-op there — the
    # ondelete="SET NULL" in models.py covers newly-created schemas (test_db_migrations gate).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # On Postgres the unnamed FK from 0001_initial is named "runs_project_id_fkey" by
        # convention (Postgres auto-names unnamed FKs as <table>_<col>_fkey).
        op.drop_constraint("runs_project_id_fkey", "runs", type_="foreignkey")
        op.create_foreign_key(
            None,
            "runs",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Restore the unnamed FK without ondelete (Postgres re-names it automatically).
        op.drop_constraint("runs_project_id_fkey", "runs", type_="foreignkey")
        op.create_foreign_key(
            None,
            "runs",
            "projects",
            ["project_id"],
            ["id"],
        )

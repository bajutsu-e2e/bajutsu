"""add ON DELETE SET NULL to runs.project_id FK (BE-0225)

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _project_id_fk_name(bind: sa.engine.Connection) -> str:
    """Reflect the actual name of the runs.project_id FK from the live schema.

    Avoids hardcoding the Postgres auto-generated name, which can vary when a
    ``naming_convention`` is added to ``Base.metadata`` in the future.
    """
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys("runs"):
        if fk["constrained_columns"] == ["project_id"]:
            name = fk["name"]
            if name is None:
                raise RuntimeError("runs.project_id FK has no name — schema out of sync?")
            return name
    raise RuntimeError("runs.project_id FK not found — schema out of sync?")


def upgrade() -> None:
    # The original FK (0001_initial) had no ondelete= action, so deleting a project with run
    # history would raise IntegrityError on Postgres. Adding ON DELETE SET NULL lets deregister
    # retain the run rows while clearing their project association — matching the documented
    # contract ("only the binding is removed; run history is retained", BE-0225).
    #
    # SQLite: FKs aren't enforced at runtime (no PRAGMA foreign_keys=ON on the gate) and the
    # dialect doesn't store named FK constraints, so the operation is a no-op there — the
    # ondelete="SET NULL" in models.py covers newly-created schemas (test_db_migrations gate).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(_project_id_fk_name(bind), "runs", type_="foreignkey")
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
        op.drop_constraint(_project_id_fk_name(bind), "runs", type_="foreignkey")
        op.create_foreign_key(
            None,
            "runs",
            "projects",
            ["project_id"],
            ["id"],
        )

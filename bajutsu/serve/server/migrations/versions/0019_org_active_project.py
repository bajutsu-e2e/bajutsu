"""persist each org's active project on its own row (BE-0393)

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Which project an org last bound, so the database-backed registry stops holding it in a
    # process-local dictionary and a restart no longer drops it (BE-0393). Nullable and unseeded:
    # an existing row reads as "no active project", exactly what a restarted process holds today.
    # No foreign key to `projects.id` — `projects.org_id` already points the other way, so a second
    # edge would need `use_alter` to keep the DDL sortable; `delete_project` clears this column
    # itself and an unresolvable id reads as "no active project".
    op.add_column("orgs", sa.Column("active_project_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "active_project_id")

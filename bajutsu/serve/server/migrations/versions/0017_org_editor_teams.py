"""widen the org's editor Team into a list (BE-0375 unit 9)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, portable JSON on the SQLite gate — the same variant `models._JSON` picks.
_JSON = sa.JSON().with_variant(JSONB, "postgresql")

# A table expression rather than raw SQL, so the value crossing the seam is serialized by the column
# type on both dialects — `json_array` and `to_jsonb` would each work on only one of them.
_orgs = sa.table(
    "orgs",
    sa.column("id", sa.String()),
    sa.column("editor_team", sa.String()),
    sa.column("editor_teams", _JSON),
)


def upgrade() -> None:
    op.add_column("orgs", sa.Column("editor_teams", _JSON, nullable=True))
    # Unlike the membership columns 0015 and 0016 added, this one carries data across: an
    # `editor_team` an admin set through `POST /api/orgs/<slug>/membership` exists nowhere but this
    # row — `membership_seeded_at` is stamped by that write, after which `seed_org_membership` is a
    # no-op forever, so no later startup would reconstruct it from the `orgs:` block. Dropping the
    # column without the copy would silently demote every editor of such an org to viewer.
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_orgs.c.id, _orgs.c.editor_team).where(_orgs.c.editor_team.is_not(None))
    ).all()
    for org_id, team in rows:
        bind.execute(_orgs.update().where(_orgs.c.id == org_id).values(editor_teams=[team]))
    op.drop_column("orgs", "editor_team")


def downgrade() -> None:
    op.add_column("orgs", sa.Column("editor_team", sa.String(), nullable=True))
    # Lossy in the one direction a widening can be: an org that gained a second writing Team keeps
    # only the first. Recorded here rather than refused, because the alternative — a downgrade that
    # raises on such a row — leaves an operator no way back at all from the very state this
    # migration was written to make reachable.
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_orgs.c.id, _orgs.c.editor_teams).where(_orgs.c.editor_teams.is_not(None))
    ).all()
    for org_id, teams in rows:
        if teams:
            bind.execute(_orgs.update().where(_orgs.c.id == org_id).values(editor_team=teams[0]))
    op.drop_column("orgs", "editor_teams")

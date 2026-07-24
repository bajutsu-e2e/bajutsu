"""RBAC helpers (BE-0015 7c-2): the role policy, the endpoint→role map, the rank check, and the
gate's `forbidden_for_role` (which reads the user's stored role)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import Base
from bajutsu.serve.state import ServeState


def test_role_for_derives_the_role_from_team_membership() -> None:
    # BE-0313: admin follows the server-wide admin Team, editor the org's editor Team, else viewer.
    teams = ["acme-gh/scenario-maintainers", "acme-gh/ops"]
    assert (
        ops.role_for(
            teams=teams, editor_team="acme-gh/scenario-maintainers", admin_team="acme-gh/ops"
        )
        == "admin"
    )
    assert (
        ops.role_for(
            teams=teams, editor_team="acme-gh/scenario-maintainers", admin_team="acme-gh/absent"
        )
        == "editor"
    )
    # The base role: signed in, but a member of neither Team.
    assert (
        ops.role_for(teams=teams, editor_team="acme-gh/absent", admin_team="acme-gh/none")
        == "viewer"
    )
    # An unset Team never matches, even against an empty-string team name in the list.
    assert ops.role_for(teams=[""], editor_team=None, admin_team=None) == "viewer"
    # Nested-Team names don't match the configured flat Team (exact match only).
    assert (
        ops.role_for(teams=["acme-gh/parent/child"], editor_team="acme-gh/parent", admin_team=None)
        == "viewer"
    )


def test_required_role_maps_endpoints() -> None:
    assert ops.required_role("GET", "/api/runs") is None  # reads need no role
    assert ops.required_role("POST", "/api/run") == "editor"
    assert ops.required_role("POST", "/api/jobs/abc/cancel") == "editor"
    assert ops.required_role("POST", "/api/jobs/abc/respond-human") == "editor"  # BE-0179 handoff
    assert ops.required_role("POST", "/api/apikey") == "admin"
    assert ops.required_role("POST", "/api/gitcredential") == "admin"  # BE-0224 secret-setting
    assert ops.required_role("POST", "/api/secrets") == "admin"  # BE-0274 scenario-secret write
    assert ops.required_role("GET", "/api/secrets") is None  # describe-only read stays ungated
    assert ops.required_role("POST", "/api/login") is None  # auth endpoints aren't role-gated
    # The one gated read: the config body is a wider disclosure than the path-only /api/config.
    assert ops.required_role("GET", "/api/config/content") == "admin"
    assert ops.required_role("GET", "/api/config") is None  # the path-only read stays ungated
    # Project hub (BE-0225): register/deregister/activate repoint a config binding (admin, like
    # /api/config); triggering a run is an editor action (like /api/run); listing / per-project runs
    # are reads.
    assert ops.required_role("GET", "/api/projects") is None
    assert ops.required_role("GET", "/api/projects/checkout/runs") is None
    assert ops.required_role("POST", "/api/projects") == "admin"
    assert ops.required_role("DELETE", "/api/projects/checkout") == "admin"
    assert ops.required_role("POST", "/api/projects/checkout/run") == "editor"
    assert ops.required_role("POST", "/api/projects/checkout/activate") == "admin"
    # Run lifecycle (BE-0239): soft-delete (DELETE), restore, and bulk-delete are editor actions;
    # permanent purge (?purge=true) is admin, but the query isn't in `path`, so its gate lives in the
    # operation, not here. The worker upload-urls POST keeps its own no-role handling.
    assert ops.required_role("DELETE", "/api/runs/20260101-000000") == "editor"
    assert ops.required_role("DELETE", "/api/crawl/runs/20260101-000000") == "editor"
    assert ops.required_role("POST", "/api/runs/20260101-000000/restore") == "editor"
    assert ops.required_role("POST", "/api/runs/bulk-delete") == "editor"
    assert ops.required_role("POST", "/api/runs/20260101-000000/upload-urls") is None
    # Per-artifact uploads (BE-0268) are admin, like /api/upload; the exists check is a GET so it
    # needs its own early case (a GET path in `_ADMIN_PATHS` alone would never gate — the generic
    # membership check only runs past the `method != "POST"` guard).
    assert ops.required_role("POST", "/api/artifacts/config") == "admin"
    assert ops.required_role("POST", "/api/artifacts/scenarios") == "admin"
    assert ops.required_role("POST", "/api/artifacts/binary") == "admin"
    assert ops.required_role("GET", "/api/artifacts/exists") == "admin"
    # Server identity (BE-0272): the version string is open, but the Git checkout (commit/branch/
    # dirty) is admin — a branch name leaks the in-progress topic. Same GET early-case reason.
    assert ops.required_role("GET", "/api/version") is None
    assert ops.required_role("GET", "/api/version/checkout") == "admin"


def test_role_allows_ranks_viewer_editor_admin() -> None:
    assert ops.role_allows("admin", "editor")
    assert ops.role_allows("editor", "editor")
    assert not ops.role_allows("viewer", "editor")
    assert not ops.role_allows("editor", "admin")


def test_forbidden_for_role_without_a_database_is_allowed(tmp_path: Path) -> None:
    assert (
        ops.forbidden_for_role(ServeState(runs_dir=tmp_path), "alice", "POST", "/api/run") is False
    )


def test_forbidden_for_role_reads_the_stored_role(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    repo.upsert_user("v", org_id="default", github_login="v", email="v@x", role="viewer")
    repo.upsert_user("e", org_id="default", github_login="e", email="e@x", role="editor")
    state = ServeState(runs_dir=tmp_path, repository=repo)
    assert ops.forbidden_for_role(state, "v", "POST", "/api/run") is True  # viewer can't run
    assert ops.forbidden_for_role(state, "e", "POST", "/api/run") is False  # editor can
    assert ops.forbidden_for_role(state, "e", "POST", "/api/apikey") is True  # editor isn't admin
    assert ops.forbidden_for_role(state, "v", "GET", "/api/runs") is False  # reads are open
    # BE-0272: version stays open to a viewer, the checkout detail is admin-only.
    assert ops.forbidden_for_role(state, "v", "GET", "/api/version") is False
    assert ops.forbidden_for_role(state, "e", "GET", "/api/version/checkout") is True
    assert ops.forbidden_for_role(state, "v", "GET", "/api/version/checkout") is True

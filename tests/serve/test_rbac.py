"""RBAC helpers (BE-0015 7c-2): the role policy, the endpoint→role map, the rank check, and the
gate's `forbidden_for_role` (which reads the user's stored role)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import Base


def test_role_for_applies_the_env_policy() -> None:
    admins, viewers = frozenset({"root"}), frozenset({"guest"})
    assert ops.role_for("root", admins=admins, viewers=viewers) == "admin"
    assert ops.role_for("guest", admins=admins, viewers=viewers) == "viewer"
    assert ops.role_for("dev", admins=admins, viewers=viewers) == "editor"  # the default


def test_required_role_maps_endpoints() -> None:
    assert ops.required_role("GET", "/api/runs") is None  # reads need no role
    assert ops.required_role("POST", "/api/run") == "editor"
    assert ops.required_role("POST", "/api/jobs/abc/cancel") == "editor"
    assert ops.required_role("POST", "/api/apikey") == "admin"
    assert ops.required_role("POST", "/api/login") is None  # auth endpoints aren't role-gated


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

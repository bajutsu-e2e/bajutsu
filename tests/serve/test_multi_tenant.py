"""BE-0015 multi-tenancy: org resolution and org-scope enforcement at the operations layer, plus
the per-org store routing and worker key prefix. Driven against a real SqlRepository on in-memory
SQLite (same thread, so the single connection is safe) and a real config file — no mock, no fixture."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import StoreBundle
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import Base
from bajutsu.serve.server.object_store import org_prefix

CONFIG = """
apps:
  demo: { bundleId: com.example.demo }
  checkout: { bundleId: com.example.checkout }
  other: { bundleId: com.example.other }

orgs:
  acme:
    members: [alice]
    apps: [demo, checkout]
  globex:
    members: [bob]
    apps: [other]
"""


def _state(tmp_path: Path, config_text: str = CONFIG) -> srv.ServeState:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(config_text, encoding="utf-8")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    for org, members in (("acme", ["alice"]), ("globex", ["bob"])):
        repo.ensure_org(org, slug=org, name=org)
        for m in members:
            repo.upsert_user(m, org_id=org, github_login=m, email=f"{m}@x")
    return srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, repository=repo)


def test_list_apps_is_scoped_to_the_actors_org(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert ops.list_apps_payload(state, actor="alice")[0] == ["checkout", "demo"]
    assert ops.list_apps_payload(state, actor="bob")[0] == ["other"]
    # No identity → the default org, which owns the apps no org claims (none here).
    assert ops.list_apps_payload(state, actor=None)[0] == []


def test_start_run_on_another_orgs_app_is_forbidden(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.start_run(
        state, {"app": "other", "scenario": "smoke.yaml"}, actor="alice"
    )
    assert status == 403
    assert payload == {"error": "forbidden"}


def test_start_run_on_own_orgs_app_passes_the_org_check(tmp_path: Path) -> None:
    # alice owns demo; the org check passes, so it fails later (no scenarios dir), not with 403.
    state = _state(tmp_path)
    _payload, status = ops.start_run(
        state, {"app": "demo", "scenario": "smoke.yaml"}, actor="alice"
    )
    assert status != 403


def test_read_scenario_in_another_orgs_app_is_not_found(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.read_scenario(state, "other", "smoke.yaml", actor="alice")
    assert status == 404
    assert payload == {"error": "not found"}


def test_no_orgs_block_keeps_a_single_tenant(tmp_path: Path) -> None:
    # Without an orgs: block every app belongs to the default org, so any user sees them all and
    # nothing is forbidden (single-tenant behavior is unchanged). The user is in the default org,
    # matching what oauth_callback would assign from this config.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "apps:\n  demo: { bundleId: com.example.demo }\n  other: { bundleId: com.example.other }\n",
        encoding="utf-8",
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="default")
    repo.upsert_user("alice", org_id="default", github_login="alice", email="a@x")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, repository=repo)

    assert ops.list_apps_payload(state, actor="alice")[0] == ["demo", "other"]
    _payload, status = ops.start_run(
        state, {"app": "other", "scenario": "smoke.yaml"}, actor="alice"
    )
    assert status != 403


class _FakeOAuthClient:
    def __init__(self, login: str) -> None:
        self._login = login

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/?state={state}"

    def fetch_login(self, code: str) -> str | None:
        return self._login


def test_oauth_login_assigns_the_org_from_config(tmp_path: Path) -> None:
    # A login resolves to its config org and is persisted there, so later requests scope to it.
    state = _state(tmp_path)
    state.oauth = _FakeOAuthClient("bob")
    state.oauth_allowed_users = frozenset({"bob"})
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    assert state.repository is not None
    assert state.repository.user_org("bob") == "globex"
    # And a brand-new allowlisted login with no org membership lands in the default org.
    state.oauth = _FakeOAuthClient("carol")
    state.oauth_allowed_users = frozenset({"carol"})
    ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert state.repository.user_org("carol") == "default"


def test_for_org_routes_to_the_per_org_bundle(tmp_path: Path) -> None:
    # A server backend sets an org_stores factory; for_org must route through it.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    seen: list[str] = []

    def factory(org: str) -> StoreBundle:
        seen.append(org)
        return StoreBundle(state.artifacts, state.scenarios, state.baselines)

    state.org_stores = factory
    state.for_org("acme")
    assert seen == ["acme"]


def test_org_prefix_namespaces_non_default_orgs() -> None:
    # The default org keeps the base prefix (single-tenant layout unchanged); others get a segment.
    assert org_prefix("", "default") == ""
    assert org_prefix("tenant/", "default") == "tenant/"
    assert org_prefix("", "acme") == "acme/"
    assert org_prefix("tenant/", "acme") == "tenant/acme/"

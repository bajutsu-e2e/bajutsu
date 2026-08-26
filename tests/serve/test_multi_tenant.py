"""BE-0015 multi-tenancy: org resolution and org-scope enforcement at the operations layer, plus
the per-org store routing and worker key prefix. Driven against a real SqlRepository on in-memory
SQLite in the gate (same thread, so the single connection is safe) and, behind the `postgres` marker,
against a real Postgres service in the serve-db.yml lane (BE-0309), plus a real config file — no
mock."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine, select

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.operations.config import seed_orgs_from_bound_config
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import AuditLog, Base
from bajutsu.serve.server.oauth import Identity
from bajutsu.serve.server.object_store import org_prefix
from bajutsu.serve.state import SessionManager, StoreBundle

CONFIG = """
targets:
  demo: { bundleId: com.example.demo }
  checkout: { bundleId: com.example.checkout }
  other: { bundleId: com.example.other }

orgs:
  acme:
    members: [alice]
    targets: [demo, checkout]
  globex:
    members: [bob]
    targets: [other]
"""


def _state(
    serve_engine: Callable[..., Engine], tmp_path: Path, config_text: str = CONFIG
) -> srv.ServeState:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(config_text, encoding="utf-8")
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, repository=repo)
    # A database-backed deployment resolves sign-in and org placement against the `orgs` table
    # (BE-0375), which `serve()` fills from the bound config at startup — and only while the table
    # is still empty, so this runs before the rows below rather than after them.
    seed_orgs_from_bound_config(state)
    for org, members in (("acme", ["alice"]), ("globex", ["bob"])):
        repo.ensure_org(org, slug=org, name=org)
        for m in members:
            repo.upsert_user(m, org_id=org, github_login=m, email=f"{m}@x")
    return state


def test_list_targets_is_scoped_to_the_actors_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _state(serve_engine, tmp_path)
    assert [a["name"] for a in ops.list_targets_payload(state, actor="alice")[0]] == [
        "checkout",
        "demo",
    ]
    assert [a["name"] for a in ops.list_targets_payload(state, actor="bob")[0]] == ["other"]
    # No identity → the default org, which owns the apps no org claims (none here).
    assert [a["name"] for a in ops.list_targets_payload(state, actor=None)[0]] == []


def test_start_run_on_another_orgs_app_is_forbidden(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _state(serve_engine, tmp_path)
    payload, status = ops.start_run(
        state, {"target": "other", "scenario": "smoke.yaml"}, actor="alice"
    )
    assert status == 403
    assert payload == {"error": "forbidden"}


def test_start_run_on_own_orgs_app_passes_the_org_check(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # alice owns demo; the org check passes, so it fails later (no scenarios dir), not with 403.
    state = _state(serve_engine, tmp_path)
    _payload, status = ops.start_run(
        state, {"target": "demo", "scenario": "smoke.yaml"}, actor="alice"
    )
    assert status != 403


def test_upload_scenarios_to_another_orgs_app_is_forbidden(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The same cross-org guard as start_run, checked before the zip is ever touched (BE-0340) — a
    # bogus zip_path is fine here, since a forbidden target never reaches read_scenario_zip.
    state = _state(serve_engine, tmp_path)
    payload, status = ops.upload_scenarios(
        state, tmp_path / "unused.zip", target="other", actor="alice"
    )
    assert status == 403
    assert payload == {"error": "forbidden"}


def test_upload_scenarios_to_own_orgs_app_passes_the_org_check(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # alice owns demo; the org check passes, so it fails later (no scenarios dir), not with 403 —
    # the same shape as test_start_run_on_own_orgs_app_passes_the_org_check above.
    state = _state(serve_engine, tmp_path)
    _payload, status = ops.upload_scenarios(
        state, tmp_path / "unused.zip", target="demo", actor="alice"
    )
    assert status != 403


def test_save_scenario_records_an_audit_entry(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The .yaml leg of the Replay-upload control (BE-0340) must leave the same audit trail
    # upload_scenarios' .zip leg already does — "scenario.save" mirroring "scenarios.upload".
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        f"targets:\n  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="default")
    repo.upsert_user("alice", org_id="default", github_login="alice", email="a@x")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, cwd=tmp_path, repository=repo)
    payload, status = ops.save_scenario(
        state,
        {"target": "demo", "path": "new.yaml", "yaml": "- name: s\n  steps: []\n"},
        actor="alice",
    )
    assert status == 200
    assert payload["overwritten"] is False
    with repo._engine.connect() as conn:
        rows = conn.execute(select(AuditLog.action, AuditLog.target)).all()
    assert ("scenario.save", "demo") in [(str(a), str(t)) for a, t in rows]


def test_read_scenario_in_another_orgs_app_is_not_found(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _state(serve_engine, tmp_path)
    payload, status = ops.read_scenario(state, "other", "smoke.yaml", actor="alice")
    assert status == 404
    assert payload == {"error": "not found"}


def test_no_orgs_block_keeps_a_single_tenant(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # Without an orgs: block every app belongs to the default org, so any user sees them all and
    # nothing is forbidden (single-tenant behavior is unchanged). The user is in the default org,
    # matching what oauth_callback would assign from this config.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n  demo: { bundleId: com.example.demo }\n  other: { bundleId: com.example.other }\n",
        encoding="utf-8",
    )
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="default")
    repo.upsert_user("alice", org_id="default", github_login="alice", email="a@x")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, repository=repo)

    assert [a["name"] for a in ops.list_targets_payload(state, actor="alice")[0]] == [
        "demo",
        "other",
    ]
    _payload, status = ops.start_run(
        state, {"target": "other", "scenario": "smoke.yaml"}, actor="alice"
    )
    assert status != 403


class _FakeOAuthClient:
    def __init__(
        self, login: str, orgs: list[str] | None = None, teams: list[str] | None = None
    ) -> None:
        self._login = login
        self._orgs = orgs or []
        self._teams = teams or []

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        return Identity(login=self._login, orgs=list(self._orgs), teams=list(self._teams))


def test_oauth_login_assigns_the_org_from_config(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A login resolves to its config org and is persisted there, so later requests scope to it.
    state = _state(serve_engine, tmp_path)
    state.auth.oauth = _FakeOAuthClient("bob")
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    assert state.repository is not None
    assert state.repository.user_org("bob") == "globex"
    # BE-0313: a login in no org's `members`/`githubOrgs` is now rejected at sign-in, not landed in
    # the default org — the org roster is the allowlist.
    state.auth.oauth = _FakeOAuthClient("carol")
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 403 and sid is None
    assert state.repository.user_org("carol") is None


def test_oauth_login_assigns_the_org_from_github_org_membership(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A login with no explicit member listing is mapped to a bajutsu org by its GitHub org.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n  demo: { bundleId: com.example.demo }\n"
        "orgs:\n  acme:\n    githubOrgs: [acme-gh]\n    targets: [demo]\n",
        encoding="utf-8",
    )
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        config=cfg,
        repository=repo,
        auth=SessionManager(oauth=_FakeOAuthClient("dave", orgs=["acme-gh"])),
    )
    seed_orgs_from_bound_config(state)  # the startup seed `serve()` runs (BE-0375)
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    assert repo.user_org("dave") == "acme"


def test_local_serve_ignores_orgs_without_a_repository(tmp_path: Path) -> None:
    # No system of record (local serve / token mode): `orgs:` is ignored — every app is listed and
    # nothing is forbidden, even for a config that declares orgs.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg)  # repository defaults to None
    assert state.repository is None
    assert [a["name"] for a in ops.list_targets_payload(state, actor="alice")[0]] == [
        "checkout",
        "demo",
        "other",
    ]
    _payload, status = ops.start_run(
        state, {"target": "other", "scenario": "smoke.yaml"}, actor="alice"
    )
    assert status != 403


def test_for_org_routes_to_the_per_org_bundle(tmp_path: Path) -> None:
    # A server backend sets an org_stores factory; for_org must route through it.
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    seen: list[str] = []

    def factory(org: str) -> StoreBundle:
        seen.append(org)
        return StoreBundle(state.artifacts, state.scenarios, state.baselines, state.secrets)

    state.org_stores = factory
    state.for_org("acme")
    assert seen == ["acme"]


def test_org_prefix_namespaces_non_default_orgs() -> None:
    # The default org keeps the base prefix (single-tenant layout unchanged); others get a segment.
    assert org_prefix("", "default") == ""
    assert org_prefix("tenant/", "default") == "tenant/"
    assert org_prefix("", "acme") == "acme/"
    assert org_prefix("tenant/", "acme") == "tenant/acme/"


def test_device_args_parses_and_validates() -> None:
    assert ops._device_args({"backend": "xcuitest", "udid": "U1"}) == ("xcuitest", "U1", None)
    assert ops._device_args({}) == ("", "", None)  # both omitted is fine
    _b, _u, err = ops._device_args({"backend": "nonsense"})
    assert err == ({"error": "unknown backend: nonsense"}, 400)
    _b, _u, err = ops._device_args({"udid": "bad udid!"})
    assert err == ({"error": "invalid udid"}, 400)


def test_bool_flag_is_tri_state() -> None:
    assert ops._bool_flag({"erase": True}, "erase") is True
    assert ops._bool_flag({"erase": False}, "erase") is False
    assert ops._bool_flag({}, "erase") is None  # unset → the CLI/scenario default applies
    assert ops._bool_flag({"erase": "yes"}, "erase") is None  # non-bool is ignored


class _CountingRepo:
    """Wraps a real SqlRepository and counts user_org calls, to assert org is resolved once."""

    def __init__(self, inner: SqlRepository) -> None:
        self._inner = inner
        self.user_org_calls = 0

    def user_org(self, user_id: str) -> str | None:
        self.user_org_calls += 1
        return self._inner.user_org(user_id)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_request_resolves_the_org_once(serve_engine: Callable[..., Engine], tmp_path: Path) -> None:
    # The org is resolved a single time per request (one DB lookup), not re-resolved by each of the
    # forbidden check / store bundle / audit (BE-0015 perf).
    engine = serve_engine()
    Base.metadata.create_all(engine)
    inner = SqlRepository(engine)
    inner.ensure_org("default", slug="default", name="default")
    inner.upsert_user("alice", org_id="default", github_login="alice", email="a@x")
    repo = _CountingRepo(inner)
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets:\n  demo: { bundleId: com.example.demo }\n", encoding="utf-8")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, repository=repo)  # type: ignore[arg-type]

    ops.list_scenarios(state, "demo", actor="alice")
    assert repo.user_org_calls == 1

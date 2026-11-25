"""Tests for organization-scoped API keys (BE-0509).

A script sends a key as a bearer token and acts as that organization, on the routes an allowlist
names and no others. The allowlist ships empty, so these tests add routes to it to reach the far
side of the gate. Both serve backends are exercised, since both must reach the same verdict.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _shared import _serve
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from bajutsu.serve import api_keys, gate, oplog
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import ApiKeyRecord, RunRecord, SqlRepository
from bajutsu.serve.server.models import ApiKey, AuditLog, Base
from bajutsu.serve.server.oauth import Identity
from bajutsu.serve.sessions import HUMAN, MACHINE, Principal, kind_from_stored
from bajutsu.serve.state import ServeState, SessionManager

_SHARED_TOKEN = "s3cret"


class _StubOAuth:
    """A non-None `oauth`, which narrows the shared token to worker routes (BE-0313)."""

    def authorize_url(self, state: str) -> str:
        return ""

    def fetch_identity(self, code: str) -> Identity | None:
        return None


@dataclass(frozen=True)
class _Reply:
    status: int
    body: Any


_Call = Callable[..., _Reply]


def _state(
    tmp_path: Path, *, database: bool = True, token: str = _SHARED_TOKEN, oauth: bool = True
) -> ServeState:
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text("targets: {}\n", encoding="utf-8")
    repository: SqlRepository | None = None
    if database:
        # A file, not `:memory:`: both backends answer on another thread.
        engine = create_engine(f"sqlite:///{tmp_path / 'serve.db'}")
        Base.metadata.create_all(engine)
        repository = SqlRepository(engine)
        for org in ("default", "acme", "globex"):
            repository.ensure_org(org, slug=org, name=org)
        for login, role in (("alice", "admin"), ("erin", "editor"), ("victor", "viewer")):
            repository.upsert_user(
                login, org_id="acme", github_login=login, email=f"{login}@x", role=role
            )
    return ServeState(
        runs_dir=tmp_path / "runs",
        config=config,
        root=tmp_path,
        cwd=tmp_path,
        repository=repository,
        auth=SessionManager(token=token, oauth=_StubOAuth() if oauth else None),
    )


@pytest.fixture(params=["stdlib", "fastapi"])
def backend(request: pytest.FixtureRequest) -> Iterator[Callable[[ServeState], _Call]]:
    """A factory turning a state into one `call(method, path, ...)` against the chosen backend."""
    cleanups: list[Callable[[], None]] = []

    def headers_for(bearer: str | None, session: str | None) -> dict[str, str]:
        headers = {}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if session is not None:
            headers["Cookie"] = f"bajutsu_session={session}"
        return headers

    def stdlib(state: ServeState) -> _Call:
        server, port = _serve(state)
        cleanups.extend([server.server_close, server.shutdown])

        def call(
            method: str,
            path: str,
            *,
            bearer: str | None = None,
            session: str | None = None,
            body: dict[str, Any] | None = None,
        ) -> _Reply:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=data,
                headers=headers_for(bearer, session),
                method=method,
            )
            try:
                with urllib.request.urlopen(req) as response:
                    return _Reply(response.status, json.loads(response.read() or b"{}"))
            except urllib.error.HTTPError as e:
                return _Reply(e.code, json.loads(e.read() or b"{}"))

        return call

    def fastapi(state: ServeState) -> _Call:
        from fastapi.testclient import TestClient

        from bajutsu.serve.server.app import make_app

        client = TestClient(make_app(state))

        def call(
            method: str,
            path: str,
            *,
            bearer: str | None = None,
            session: str | None = None,
            body: dict[str, Any] | None = None,
        ) -> _Reply:
            response = client.request(method, path, json=body, headers=headers_for(bearer, session))
            return _Reply(response.status_code, response.json() if response.content else {})

        return call

    yield stdlib if request.param == "stdlib" else fastapi
    # Reversed, so a server is shut down before it is closed.
    for cleanup in reversed(cleanups):
        cleanup()


def _issue(state: ServeState, org: str = "acme", role: str = "editor") -> tuple[str, str]:
    """A fresh key for *org*: `(identifier, bearer value)`."""
    payload, status = ops.create_api_key(
        state, org, {"name": "laptop", "role": role}, actor="alice"
    )
    assert status == 200, payload
    return payload["id"], payload["key"]


def _repository(state: ServeState) -> SqlRepository:
    assert isinstance(state.repository, SqlRepository)
    return state.repository


def _audit_rows(state: ServeState) -> list[AuditLog]:
    with Session(_repository(state)._engine) as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.at, AuditLog.id)))


def _open_routes(monkeypatch: pytest.MonkeyPatch, *routes: tuple[str, str]) -> None:
    monkeypatch.setattr(gate, "_API_KEY_PATHS", frozenset(routes))


# --- format and storage -------------------------------------------------------------------------


def test_a_generated_key_splits_back_into_its_identifier_and_secret() -> None:
    # The secret is url-safe base64, so it may hold `_` itself: only the first two splits count.
    for _ in range(50):
        key = api_keys.new_key()
        parsed = api_keys.parse_key(key.value)
        assert parsed is not None
        assert parsed[0] == key.id
        assert api_keys.hash_secret(parsed[1]) == key.secret_hash


@pytest.mark.parametrize(
    "value",
    [
        "",
        "s3cret",
        "bajutsu_",
        "bajutsu_token",
        "bajutsu_nothexnothexnoth_secret",
        "bajutsu_0123456789abcdef_",
        "other_0123456789abcdef_secret",
    ],
)
def test_a_value_without_the_key_shape_is_not_a_key(value: str) -> None:
    assert api_keys.parse_key(value) is None


def test_issuing_returns_the_key_once_and_stores_no_secret(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.create_api_key(
        state, "acme", {"name": "laptop", "role": "viewer"}, actor="alice"
    )
    assert status == 200
    secret = payload["key"].split("_", 2)[2]
    with Session(_repository(state)._engine) as session:
        row = session.scalars(select(ApiKey)).one()
        stored = [str(getattr(row, column.name)) for column in ApiKey.__table__.columns]
    assert not any(secret in value for value in stored)
    listing, status = ops.list_api_keys(state, "acme")
    assert status == 200
    assert secret not in json.dumps(listing)
    assert listing["keys"] == [
        {
            "id": payload["id"],
            "name": "laptop",
            "role": "viewer",
            "createdBy": "alice",
            "expiresAt": payload["expiresAt"],
            "lastUsedAt": None,
        }
    ]


def test_the_expiry_defaults_to_ninety_days(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, _status = ops.create_api_key(state, "acme", {"name": "n", "role": "editor"})
    expires_in = datetime.fromisoformat(payload["expiresAt"]) - datetime.now(UTC)
    assert timedelta(days=89) < expires_in <= timedelta(days=90)


@pytest.mark.parametrize(
    "body",
    [
        {"role": "viewer"},
        {"name": "   ", "role": "viewer"},
        {"name": "x" * 101, "role": "viewer"},
        {"name": "n"},
        {"name": "n", "role": "admin"},
        {"name": "n", "role": ["viewer"]},
        {"name": "n", "role": "viewer", "expiresInDays": 0},
        {"name": "n", "role": "viewer", "expiresInDays": 366},
        {"name": "n", "role": "viewer", "expiresInDays": True},
        {"name": "n", "role": "viewer", "expiresInDays": "30"},
    ],
)
def test_a_malformed_request_issues_nothing(tmp_path: Path, body: dict[str, Any]) -> None:
    state = _state(tmp_path)
    _payload, status = ops.create_api_key(state, "acme", body)
    assert status == 400
    assert _repository(state).list_api_keys("acme") == []


def test_the_key_operations_refuse_an_unknown_org(tmp_path: Path) -> None:
    state = _state(tmp_path)
    body = {"name": "n", "role": "viewer"}
    assert ops.create_api_key(state, "nobody", body)[1] == 404
    assert ops.list_api_keys(state, "nobody")[1] == 404
    assert ops.revoke_api_key(state, "nobody", "0123456789abcdef")[1] == 404


def test_without_a_database_the_key_routes_answer_409(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    call = backend(_state(tmp_path, database=False, oauth=False))
    body = {"name": "n", "role": "viewer"}
    assert call("POST", "/api/orgs/acme/keys", bearer=_SHARED_TOKEN, body=body).status == 409
    assert call("GET", "/api/orgs/acme/keys", bearer=_SHARED_TOKEN).status == 409
    assert call("DELETE", "/api/orgs/acme/keys/x", bearer=_SHARED_TOKEN).status == 409
    # A value shaped like a key is just a wrong token there.
    assert call("GET", "/api/runs", bearer=api_keys.new_key().value).status == 401


# --- the admin routes ---------------------------------------------------------------------------


def test_an_admin_issues_lists_and_revokes_over_http(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    state = _state(tmp_path)
    admin = state.auth.issue_session("alice")
    call = backend(state)
    issued = call(
        "POST",
        "/api/orgs/acme/keys",
        session=admin,
        body={"name": "laptop", "role": "editor", "expiresInDays": 7},
    )
    assert issued.status == 200
    key_id = issued.body["id"]
    listed = call("GET", "/api/orgs/acme/keys", session=admin)
    assert [k["id"] for k in listed.body["keys"]] == [key_id]
    assert call("DELETE", f"/api/orgs/acme/keys/{key_id}", session=admin).status == 200
    assert call("GET", "/api/orgs/acme/keys", session=admin).body == {"keys": []}
    # Already gone.
    assert call("DELETE", f"/api/orgs/acme/keys/{key_id}", session=admin).status == 404


@pytest.mark.parametrize("login", ["victor", "erin"])
def test_a_viewer_and_an_editor_are_refused_every_key_route(
    tmp_path: Path, backend: Callable[[ServeState], _Call], login: str
) -> None:
    state = _state(tmp_path)
    key_id, _key = _issue(state)
    session = state.auth.issue_session(login)
    call = backend(state)
    body = {"name": "n", "role": "viewer"}
    assert call("POST", "/api/orgs/acme/keys", session=session, body=body).status == 403
    assert call("GET", "/api/orgs/acme/keys", session=session).status == 403
    assert call("DELETE", f"/api/orgs/acme/keys/{key_id}", session=session).status == 403
    assert [k.id for k in _repository(state).list_api_keys("acme")] == [key_id]


def test_deleting_another_orgs_key_answers_404_and_leaves_it_live(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    state = _state(tmp_path)
    key_id, _key = _issue(state, "globex")
    admin = state.auth.issue_session("alice")
    call = backend(state)
    assert call("DELETE", f"/api/orgs/acme/keys/{key_id}", session=admin).status == 404
    assert [k.id for k in _repository(state).list_api_keys("globex")] == [key_id]


# --- the gate -----------------------------------------------------------------------------------


def test_a_key_reaches_an_allowlisted_route_in_its_own_org(
    tmp_path: Path,
    backend: Callable[[ServeState], _Call],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    for org in ("default", "acme"):
        _repository(state).record_run(
            RunRecord(id=f"run-{org}", org_id=org, status="done", summary={"id": f"run-{org}"})
        )
    _open_routes(monkeypatch, ("GET", "/api/runs"))
    _key_id, key = _issue(state)
    call = backend(state)
    reply = call("GET", "/api/runs", bearer=key)
    assert reply.status == 200
    assert [run["id"] for run in reply.body] == ["run-acme"]
    assert call("GET", "/api/config", bearer=key).status == 403


def test_the_allowlist_ships_empty_so_a_key_opens_nothing(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    state = _state(tmp_path)
    _key_id, key = _issue(state)
    assert frozenset() == gate._API_KEY_PATHS
    call = backend(state)
    assert call("GET", "/api/runs", bearer=key).status == 403
    assert call("POST", "/api/run", bearer=key, body={}).status == 403


def test_a_key_is_not_a_login_even_with_oauth_off(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    """Without OAuth the shared token reaches everything; a key still reaches only its list."""
    state = _state(tmp_path, oauth=False)
    _key_id, key = _issue(state)
    assert backend(state)("GET", "/api/runs", bearer=key).status == 403


def test_a_key_cannot_reach_the_key_routes(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    state = _state(tmp_path)
    key_id, key = _issue(state)
    call = backend(state)
    body = {"name": "n", "role": "viewer"}
    assert call("POST", "/api/orgs/acme/keys", bearer=key, body=body).status == 403
    assert call("GET", "/api/orgs/acme/keys", bearer=key).status == 403
    assert call("DELETE", f"/api/orgs/acme/keys/{key_id}", bearer=key).status == 403


def test_a_viewer_key_cannot_call_an_editor_route(
    tmp_path: Path,
    backend: Callable[[ServeState], _Call],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    _open_routes(monkeypatch, ("POST", "/api/run"))
    _viewer_id, viewer = _issue(state, role="viewer")
    _editor_id, editor = _issue(state, role="editor")
    call = backend(state)
    assert call("POST", "/api/run", bearer=viewer, body={}).status == 403
    # Past the gate, so the operation answers for the empty body.
    assert call("POST", "/api/run", bearer=editor, body={}).status not in (401, 403)


def test_a_key_outranks_a_session_cookie_sent_with_it(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    state = _state(tmp_path)
    _key_id, key = _issue(state)
    admin = state.auth.issue_session("alice")
    assert backend(state)("GET", "/api/orgs", bearer=key, session=admin).status == 403


@pytest.mark.parametrize("flaw", ["expired", "revoked", "wrong-secret", "org-gone"])
def test_a_key_that_fails_its_check_answers_401(
    tmp_path: Path,
    backend: Callable[[ServeState], _Call],
    monkeypatch: pytest.MonkeyPatch,
    flaw: str,
) -> None:
    state = _state(tmp_path)
    repository = _repository(state)
    _open_routes(monkeypatch, ("GET", "/api/runs"))
    key_id, key = _issue(state)
    call = backend(state)
    assert call("GET", "/api/runs", bearer=key).status == 200
    if flaw == "expired":
        with Session(repository._engine) as session:
            row = session.get(ApiKey, key_id)
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
    elif flaw == "revoked":
        assert ops.revoke_api_key(state, "acme", key_id)[1] == 200
    elif flaw == "wrong-secret":
        key = f"bajutsu_{key_id}_not-the-secret"
    else:
        # The soft delete alone, so the refusal rests on `get_org` rather than on the revocation.
        assert repository.soft_delete_org("acme", at=datetime.now(UTC))
    reply = call("GET", "/api/runs", bearer=key)
    assert (reply.status, reply.body) == (401, {"error": "unauthorized"})


def test_retiring_an_org_revokes_its_keys(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    state = _state(tmp_path)
    _key_id, key = _issue(state)
    _other_id, _other = _issue(state, "globex")
    assert ops.delete_org(state, "acme", actor="alice")[1] == 200
    assert backend(state)("GET", "/api/runs", bearer=key).status == 401
    assert _repository(state).list_api_keys("acme") == []
    assert len(_repository(state).list_api_keys("globex")) == 1
    delete = next(row for row in _audit_rows(state) if row.action == "org.delete")
    assert delete.detail["apiKeysRevoked"] == 1


def test_a_shared_token_shaped_like_a_key_still_authenticates_a_worker(
    tmp_path: Path, backend: Callable[[ServeState], _Call]
) -> None:
    token = "bajutsu_0123456789abcdef_operator-chosen"
    state = _state(tmp_path, token=token)
    reply = backend(state)(
        "POST", "/api/worker/lease", bearer=token, body={"worker_id": "w1", "capabilities": []}
    )
    assert reply.status not in (401, 403)


def test_only_the_bearer_check_builds_a_key_principal() -> None:
    """A session row claiming the key kind reads as a machine, and no stored field sets a role."""
    assert kind_from_stored("api-key") == MACHINE
    stored = Principal.from_stored("api-key:some-id", "acme", "api-key")
    assert (stored.kind, stored.role) == (MACHINE, None)
    assert Principal.from_stored("alice", None, HUMAN).role is None


def test_a_key_with_no_role_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_routes(monkeypatch, ("GET", "/api/runs"))
    assert gate.forbidden_for_api_key("GET", "/api/runs", role=None)
    assert not gate.forbidden_for_api_key("GET", "/api/runs", role="viewer")


# --- audit and last use -------------------------------------------------------------------------


def test_issue_use_and_revoke_are_audited_by_identifier_alone(
    tmp_path: Path,
    backend: Callable[[ServeState], _Call],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    _open_routes(monkeypatch, ("GET", "/api/runs"))
    key_id, key = _issue(state)
    call = backend(state)
    assert call("GET", "/api/runs", bearer=key).status == 200
    assert call("GET", "/api/config", bearer=key).status == 403
    assert ops.revoke_api_key(state, "acme", key_id, actor="alice")[1] == 200

    rows = [row for row in _audit_rows(state) if row.action.startswith("apikey.")]
    # Sorted: `at` is the database's clock, which on SQLite only resolves to the second.
    assert sorted(row.action for row in rows) == [
        "apikey.issue",
        "apikey.revoke",
        "apikey.use",
        "apikey.use",
    ]
    refused, allowed = sorted(
        (row for row in rows if row.action == "apikey.use"), key=lambda row: row.detail["allowed"]
    )
    assert (allowed.actor_id, allowed.org_id, allowed.target) == (None, "acme", "/api/runs")
    assert allowed.detail == {"method": "GET", "allowed": True, "apiKey": key_id}
    assert refused.detail["allowed"] is False
    assert all(row.detail["apiKey"] == key_id for row in rows)
    secret = key.split("_", 2)[2]
    assert not any(secret in f"{row.target}{json.dumps(row.detail)}" for row in rows)


def test_a_use_is_logged_under_the_key_and_never_with_its_secret(
    tmp_path: Path,
    backend: Callable[[ServeState], _Call],
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = _state(tmp_path)
    key_id, key = _issue(state)
    caplog.handler.addFilter(oplog._ContextFilter())
    with caplog.at_level(logging.INFO):
        backend(state)("GET", "/api/runs", bearer=key)
    use = next(r for r in caplog.records if getattr(r, "event", "") == "apikey.use")
    assert getattr(use, "actor", None) == f"api-key:{key_id}"
    assert key.split("_", 2)[2] not in caplog.text


def test_the_last_used_time_moves_at_most_once_a_minute(
    serve_engine: Callable[..., Engine],
) -> None:
    engine = serve_engine(foreign_keys=True)
    Base.metadata.create_all(engine)
    repository = SqlRepository(engine)
    repository.ensure_org("acme", slug="acme", name="acme")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    repository.create_api_key(
        ApiKeyRecord(
            id="k",
            org_id="acme",
            name="n",
            role="viewer",
            secret_hash="h",
            expires_at=start + timedelta(days=1),
        )
    )
    minute = timedelta(minutes=1)

    def touch(seconds: int) -> datetime | None:
        repository.touch_api_key("k", at=start + timedelta(seconds=seconds), min_interval=minute)
        key = repository.get_api_key("k")
        assert key is not None
        return key.last_used_at

    assert touch(0) == start
    assert touch(59) == start
    assert touch(60) == start + minute

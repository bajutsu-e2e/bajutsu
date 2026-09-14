"""Tests for the OIDC exchange endpoint and the machine session it mints (BE-0414 unit 1).

The third caller shape, end to end: a CI job presents its OIDC token once, `serve` verifies it and
mints a short-lived machine session, and every later call in the pipeline is an ordinary session
request. Offline throughout — the issuer's keys are generated in-process and injected through
`JwksCache`'s `fetch` seam, so no test reaches the network.

What a machine session may *do* is BE-0414 unit 3's endpoint allowlist, covered in the last two
sections: the pipeline's own sequence is open and everything else is refused by not being named,
the verified org travels with the session rather than through `org_of`, and an operator can end a
repository's outstanding sessions without retiring its org.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from _shared import _serve
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from sqlalchemy import Engine

from bajutsu.serve import operations as ops
from bajutsu.serve.oidc import GITHUB_ACTIONS, JwksCache, OidcConfig
from bajutsu.serve.sessions import MACHINE, InMemorySessionStore
from bajutsu.serve.state import ServeState, SessionManager

ISSUER = GITHUB_ACTIONS.issuer
AUDIENCE = "https://bajutsu.example.com"
_ORGS_YAML = """
targets: {}

orgs:
  acme:
    members: [alice]
    allowedRepositories:
      - acme/app
      - repository: acme/web
        environment: production
  globex:
    members: [bob]
    allowedRepositories: [shared/ci]
  shared:
    allowedRepositories: [shared/ci]
"""


def _threaded(tmp_path: Path) -> Callable[..., Engine]:
    """A `serve_engine`-shaped factory backed by a file rather than `:memory:`.

    The HTTP cases below serve on a background thread, and SQLite's in-memory database is
    per-connection — so the server thread would open a fresh, empty one and find no tables. The
    exchange itself is exercised against the parametrized fixture (Postgres included) above;
    these cases are about the gate, so a file is enough.
    """
    from sqlalchemy import create_engine

    def factory(*, foreign_keys: bool = False) -> Engine:
        return create_engine(f"sqlite:///{tmp_path / 'serve.db'}")

    return factory


def _key(kid: str = "k1") -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "alg": "RS256"}, private=True)


def _fetch_for(key: RSAKey) -> Callable[[str], bytes]:
    public = KeySet([RSAKey.import_key(key.as_dict(private=False))]).as_dict()

    def fetch(url: str) -> bytes:
        if url.endswith("/.well-known/openid-configuration"):
            return json.dumps({"jwks_uri": f"{ISSUER}/.well-known/jwks"}).encode()
        return json.dumps(public).encode()

    return fetch


def _token(key: RSAKey, **claims: Any) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "jti": f"jti-{now}-{claims.get('repository', 'acme/app')}",
        "repository": "acme/app",
        "repository_owner": "acme",
        "ref": "refs/heads/main",
        "job_workflow_ref": "acme/app/.github/workflows/e2e.yml@refs/heads/main",
        **claims,
    }
    return jwt.encode({"alg": "RS256", "kid": key.kid}, payload, key)


def _state(
    serve_engine: Callable[..., Engine],
    tmp_path: Path,
    key: RSAKey,
    *,
    oidc: bool = True,
    database: bool = True,
    **config: Any,
) -> ServeState:
    from bajutsu.serve.operations.config import seed_orgs_from_bound_config
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base
    from bajutsu.serve.server.sessions import SqlSessionStore

    path = tmp_path / "bajutsu.config.yaml"
    path.write_text(_ORGS_YAML, encoding="utf-8")
    settings = (
        OidcConfig(
            provider=GITHUB_ACTIONS,
            audience=AUDIENCE,
            issuer=ISSUER,
            max_token_age=config.pop("max_token_age", 300),
            session_ttl=config.pop("session_ttl", 900),
        )
        if oidc
        else None
    )
    repository = sessions = None
    if database:
        engine = serve_engine(foreign_keys=True)
        Base.metadata.create_all(engine)
        repository = SqlRepository(engine)
        sessions = SqlSessionStore(engine)
    state = ServeState(
        runs_dir=tmp_path / "runs",
        config=path,
        root=tmp_path,
        cwd=tmp_path,
        repository=repository,
        auth=SessionManager(
            token="s3cret",
            oidc=settings,
            sessions=sessions if sessions is not None else InMemorySessionStore(),
        ),
        # The injection point for an offline issuer: the operation builds one lazily only when the
        # state carries none, so pre-seeding it is how a test keeps the key fetch in-process.
        oidc_keys=JwksCache(ISSUER, fetch=_fetch_for(key)),
    )
    if database:
        seed_orgs_from_bound_config(state)
    return state


# --- the exchange itself ----------------------------------------------------------------------


def test_a_listed_repository_exchanges_into_the_org_it_named(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 200 and sid is not None
    assert payload == {"ok": True, "org": "acme", "repository": "acme/app"}
    principal = state.auth.principal(sid)
    assert principal is not None
    # A reserved identity form no GitHub login can collide with (a login cannot contain "/"), and
    # non-None so the session is revocable at all — `revoke_identities` never touches one with none.
    assert principal.identity == "repo:acme/app"
    assert principal.org == "acme" and principal.kind == MACHINE
    assert state.auth.sessions.revoke_identities(["repo:acme/app"]) == 1


def test_naming_an_org_selects_and_never_grants(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The request says which org to check the claims against; that org's own roster is what
    admits it. Otherwise naming an org would be the grant, not the check."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    _payload, status, sid = ops.oidc_exchange(state, _token(key), "globex")
    assert status == 403 and sid is None


def test_one_repository_can_serve_several_orgs(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """A shared pipeline repository testing apps owned by different teams — the shape that forces
    the request to name its org rather than letting serve infer one."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    for org in ("globex", "shared"):
        payload, status, sid = ops.oidc_exchange(
            state, _token(key, repository="shared/ci", jti=f"jti-{org}"), org
        )
        assert status == 200 and sid is not None, org
        assert payload["org"] == org


def test_an_exchange_naming_no_org_is_refused(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    payload, status, sid = ops.oidc_exchange(state, _token(key), "")
    assert status == 400 and sid is None and "org is required" in payload["error"]


def test_an_unlisted_repository_and_a_prefix_match_are_both_refused(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    for repository in ("acme/other", "acme/app-evil"):
        _payload, status, sid = ops.oidc_exchange(
            state, _token(key, repository=repository, jti=repository), "acme"
        )
        assert status == 403 and sid is None, repository


def test_a_per_entry_environment_bound_applies_to_its_own_entry_only(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    # `acme/web` is listed under an environment bound; a token declaring none is refused.
    _payload, status, _sid = ops.oidc_exchange(
        state, _token(key, repository="acme/web", jti="web-none"), "acme"
    )
    assert status == 403
    _payload, status, sid = ops.oidc_exchange(
        state,
        _token(key, repository="acme/web", environment="production", jti="web-prod"),
        "acme",
    )
    assert status == 200 and sid is not None
    # `acme/app` is listed with no bound and still exchanges, so the bound never leaked to the org.
    assert ops.oidc_exchange(state, _token(key, jti="app"), "acme")[1] == 200


def test_a_token_for_another_audience_is_refused(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    token = _token(key, aud="https://someone-else.example.com")
    _payload, status, sid = ops.oidc_exchange(state, token, "acme")
    assert status == 403 and sid is None


def test_a_token_is_exchanged_once_and_replay_is_refused(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    token = _token(key)
    assert ops.oidc_exchange(state, token, "acme")[1] == 200
    _payload, status, sid = ops.oidc_exchange(state, token, "acme")
    assert status == 403 and sid is None


def test_replay_is_refused_across_replicas(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The reason the replay cache is a table: a hosted control plane is several replicas over one
    database, so a captured token presented to the second must be refused by the first's write."""
    from bajutsu.serve.server.db import SqlRepository

    key = _key()
    first = _state(serve_engine, tmp_path, key)
    assert first.repository is not None
    engine = first.repository._engine  # type: ignore[attr-defined]
    second = _state(serve_engine, tmp_path, key)
    second.repository = SqlRepository(engine)  # a second replica on the same database

    token = _token(key)
    assert ops.oidc_exchange(first, token, "acme")[1] == 200
    assert ops.oidc_exchange(second, token, "acme")[1] == 403


def test_the_minted_session_never_outlives_the_tokens_own_exp(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """Both are bearer credentials over TLS, so what the exchange actually buys is a shorter
    lifetime — which a session outliving its token would give straight back."""
    from datetime import UTC, datetime

    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import SessionRecord

    key = _key()
    # A session TTL far beyond the token's 300s `exp`: the token's own lifetime must win.
    state = _state(serve_engine, tmp_path, key, session_ttl=86_400)
    _payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 200 and sid is not None
    assert state.repository is not None
    with Session(state.repository._engine) as session:  # type: ignore[attr-defined]
        row = session.get(SessionRecord, sid)
        assert row is not None
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    assert expires <= datetime.now(UTC).replace(microsecond=0) + __import__("datetime").timedelta(
        seconds=301
    )


def test_a_token_already_past_its_exp_mints_no_session(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The lifetime checks tolerate 60s of clock skew, so a token can pass them with its `exp`
    already behind our clock. The session is capped by that `exp`, so it would be born dead — and
    a 200 carrying a cookie that 401s on the next call is worse than a refusal."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    now = int(time.time())
    token = _token(key, iat=now - 30, nbf=now - 30, exp=now - 5, jti="nearly-expired")
    payload, status, sid = ops.oidc_exchange(state, token, "acme")
    assert status == 403 and sid is None
    assert payload["error"] == "the OIDC token was refused"


def test_a_deployment_with_no_expected_audience_refuses_the_exchange(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """Fail closed, never "accept whichever audience it carries": the workflow picks its own, so a
    deployment accepting any would accept a token minted for an unrelated service."""
    key = _key()
    state = _state(serve_engine, tmp_path, key, oidc=False)
    payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 404 and sid is None and payload["error"] == "oidc not configured"


def test_a_deployment_with_no_database_refuses_the_exchange(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """There is nowhere to spend a `jti` without one, and exchanging without single-use would
    leave the serve-side age ceiling as the only bound on a captured token."""
    key = _key()
    state = _state(serve_engine, tmp_path, key, database=False)
    payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 400 and sid is None and "needs a database" in payload["error"]


def test_every_refusal_answers_the_same_way(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """A caller probing the endpoint learns only that it failed. The audience mismatch and the
    unlisted repository are exactly the two an attacker would otherwise enumerate; the cause goes
    to the deployment's own log instead."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    answers = {
        ops.oidc_exchange(state, _token(key, aud="elsewhere", jti="a"), "acme")[0]["error"],
        ops.oidc_exchange(state, _token(key, repository="acme/nope", jti="b"), "acme")[0]["error"],
        ops.oidc_exchange(state, _token(key, jti="c"), "no-such-org")[0]["error"],
        ops.oidc_exchange(state, "not-a-jwt", "acme")[0]["error"],
    }
    assert len(answers) == 1


# --- the gate: reaching the exchange, and what its session may do -------------------------------


@pytest.mark.parametrize("oidc", [True, False])
def test_the_exchange_is_reachable_only_when_oidc_is_configured(tmp_path: Path, oidc: bool) -> None:
    """It is the machine's way *in*, so it needs no prior serve credential — but only on a
    deployment that configured one. Elsewhere it stays behind the 401 like any other route."""
    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key, oidc=oidc)
    server, port = _serve(state)
    try:
        status, _headers, _body = _post(port, "/api/oidc/exchange", {"token": "x", "org": "acme"})
        # Configured: reached, and refused on the token (403). Not configured: never reached (401).
        assert status == (403 if oidc else 401)
    finally:
        server.shutdown()
        server.server_close()


def test_the_exchange_sets_a_session_cookie_over_http(tmp_path: Path) -> None:
    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    server, port = _serve(state)
    try:
        status, headers, _body = _post(
            port, "/api/oidc/exchange", {"token": _token(key), "org": "acme"}
        )
        assert status == 200
        cookie = headers.get("Set-Cookie", "")
        assert cookie.startswith("bajutsu_session=")
        # The pipeline holds an opaque id, never the credential it was exchanged for.
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    finally:
        server.shutdown()
        server.server_close()


def test_a_machine_session_reaches_its_allowlist_and_nothing_else(tmp_path: Path) -> None:
    """The allowlist is the whole of what governs a machine principal (BE-0414 unit 3): the
    pipeline's own sequence is open, and everything else is refused by not being named."""
    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    _payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 200 and sid is not None
    server, port = _serve(state)
    try:
        assert _get(port, "/api/runs", cookie=sid)[0] == 200
        assert _get(port, "/api/artifacts/exists?kind=binary&sha256=" + "a" * 64, cookie=sid)[
            0
        ] == (200)
        for path in ("/api/config", "/api/orgs", "/api/config/content", "/api/compose/current"):
            code, _headers, _body = _get(port, path, cookie=sid)
            assert code == 403, path
        # The operator-secret and config-rebinding writes, the four the allowlist most deliberately
        # withholds: granting the admin *rank* instead would have carried every one of them.
        for path in ("/api/config", "/api/compose", "/api/apikey", "/api/claudecodetoken"):
            code, _headers, _body = _post(port, path, {}, cookie=sid)
            assert code == 403, path
    finally:
        server.shutdown()
        server.server_close()


def test_a_machine_principal_carrying_no_org_is_refused_outright(tmp_path: Path) -> None:
    """Only the exchange ever sets an org, so a machine principal without one is a session row this
    version does not understand (`kind_from_stored`). Every allowlisted operation scopes itself by
    that org, so admitting one with none would act as the `default` tenant."""
    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    # What an unrecognized `kind` column reads back as: machine, and carrying no org.
    sid = state.auth.issue_session("repo:acme/app", org=None, kind=MACHINE)
    server, port = _serve(state)
    try:
        # On the allowlist, and still refused — the org check runs ahead of the path check.
        assert _get(port, "/api/runs", cookie=sid)[0] == 403
    finally:
        server.shutdown()
        server.server_close()


def test_a_human_session_is_untouched_by_the_machine_gate(tmp_path: Path) -> None:
    """The kind the store recorded is what routes the request, not the shape of an identity string
    — so a person signing in is governed by the role gate exactly as before."""
    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    sid = state.auth.issue_session()  # the shared-token login: a human session, no identity
    server, port = _serve(state)
    try:
        assert _get(port, "/api/runs", cookie=sid)[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_both_backends_gate_a_machine_session_identically(tmp_path: Path) -> None:
    """`gate.py` owns the policy so the stdlib handler and the FastAPI app cannot diverge on it —
    the divergence BE-0253 exists to prevent, on a security-relevant branch."""
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    _payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 200 and sid is not None
    client = TestClient(make_app(state))
    client.cookies.set("bajutsu_session", sid)
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/config").status_code == 403
    assert client.post("/api/compose", json={}).status_code == 403
    # And the exchange itself is reachable on this backend too, with no prior credential — from a
    # client carrying *no* cookie, or the 403 would be the machine gate's rather than the token's.
    assert (
        TestClient(make_app(state))
        .post("/api/oidc/exchange", json={"token": "x", "org": "acme"})
        .status_code
        == 403
    )


def test_an_open_path_is_reachable_with_a_machine_cookie_on_both_backends(
    tmp_path: Path,
) -> None:
    """`is_open` short-circuits *both* gates, and both backends must agree on that. A pipeline
    reusing one cookie jar re-exchanges to renew past its TTL, so a backend that ran the machine
    gate over the open paths would lock it out permanently on exactly that renewal."""
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    _payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 200 and sid is not None

    client = TestClient(make_app(state))
    client.cookies.set("bajutsu_session", sid)
    assert client.get("/").status_code == 200
    # A fresh token each time — the replay cache spends each `jti` once, which is the point.
    renewed = client.post(
        "/api/oidc/exchange", json={"token": _token(key, jti="renew-asgi"), "org": "acme"}
    )
    assert renewed.status_code == 200, "a machine session must be able to renew itself"

    server, port = _serve(state)
    try:
        assert _get(port, "/", cookie=sid)[0] == 200
        code, _headers, _body = _post(
            port,
            "/api/oidc/exchange",
            {"token": _token(key, jti="renew-stdlib"), "org": "acme"},
            cookie=sid,
        )
        assert code == 200
    finally:
        server.shutdown()
        server.server_close()


def test_a_machine_session_posting_a_body_is_refused_and_the_connection_stays_usable(
    tmp_path: Path,
) -> None:
    """The stdlib gate drains a refused request's body before replying, or the unread bytes
    corrupt the next request on a keep-alive connection — a request shape no GET can reach."""
    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    _payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 200 and sid is not None
    server, port = _serve(state)
    try:
        # Refused by the allowlist rather than by a role, and carrying a body worth draining.
        code, _headers, _body = _post(port, "/api/compose", {"config": "x" * 5000}, cookie=sid)
        assert code == 403
        # The next request on a fresh connection must still be answered correctly.
        assert _get(port, "/api/runs", cookie=sid)[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_the_key_set_cache_is_built_once_and_kept(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Held on the state, not rebuilt per call: a fresh cache per exchange would fetch the issuer's
    keys every time and make the refresh floor bound nothing."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    # `JwksCache.__init__` resolves `_fetch_url` at construction, so stubbing the module attribute
    # is what keeps the cold-start path in-process. Without it this is the one case in the file
    # that reaches the real issuer — and the outcome would then depend on whether the sandbox has
    # network, which is why the assertion can be exact only once the fetch is stubbed.
    monkeypatch.setattr("bajutsu.serve.oidc._fetch_url", _fetch_for(key))
    state.oidc_keys = None  # the real cold-start path, which pre-seeding otherwise hides
    assert ops.oidc_exchange(state, _token(key, jti="one"), "acme")[1] == 200
    first = state.oidc_keys
    assert first is not None
    ops.oidc_exchange(state, _token(key, jti="two"), "acme")
    assert state.oidc_keys is first


def test_the_fastapi_backend_mints_the_same_machine_session(tmp_path: Path) -> None:
    """Both backends run the exchange through the same operation, so a pipeline pointed at either
    gets the same cookie — the parity `gate.py` and the shared route registry exist to hold."""
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    client = TestClient(make_app(state))
    response = client.post("/api/oidc/exchange", json={"token": _token(key), "org": "acme"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "org": "acme", "repository": "acme/app"}
    sid = response.cookies.get("bajutsu_session")
    assert sid is not None
    # The same cookie attributes the stdlib backend sets. A CI credential is a worse thing to
    # expose to script than a browser login, and this is exactly the axis BE-0253 exists to hold.
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie.replace("Strict", "strict")
    principal = state.auth.principal(sid)
    assert principal is not None
    assert principal.kind == MACHINE and principal.org == "acme"


def test_the_fastapi_backend_leaves_the_exchange_closed_with_no_oidc_configured(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key, oidc=False)
    client = TestClient(make_app(state))
    # Never reached: `gate.is_open` leaves the path closed, so the gate answers before the route.
    assert client.post("/api/oidc/exchange", json={"token": "x", "org": "a"}).status_code == 401


def _post(
    port: int, path: str, payload: dict[str, Any], *, cookie: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    import urllib.error
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if cookie is not None:
        headers["Cookie"] = f"bajutsu_session={cookie}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _get(port: int, path: str, *, cookie: str) -> tuple[int, dict[str, str], bytes]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"Cookie": f"bajutsu_session={cookie}"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_the_gate_derives_identity_from_the_same_read_as_the_kind(tmp_path: Path) -> None:
    """The kind and the identity must come from one snapshot of the session.

    Asking separately let a session that stopped validating in between answer None to *both* —
    neither machine nor human — and the request was then served as the identity-less shared-token
    caller, which is full access. A human session refused by the role gate is what discriminates:
    the old code reached `actor_for` -> `identity()` for the login after already reading the
    principal, so a second read would show up here.
    """
    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    assert state.repository is not None
    # An editor, on an admin-only path: the request is refused inside the gate and never reaches a
    # route handler, whose own `ctx.actor()` would otherwise read the identity legitimately and
    # make the count say nothing about the gate.
    state.repository.upsert_user(
        "dana", org_id="acme", github_login="dana", email="dana@x", role="editor"
    )
    sid = state.auth.issue_session("dana")

    reads: list[str] = []
    inner = state.auth.sessions

    class _Counting:
        """Wraps the store so the gate's own reads are visible, without changing any answer."""

        def __getattr__(self, name: str) -> Any:
            attribute = getattr(inner, name)
            if name not in ("principal", "identity", "valid"):
                return attribute

            def counted(*args: Any, **kwargs: Any) -> Any:
                reads.append(name)
                return attribute(*args, **kwargs)

            return counted

    state.auth.sessions = _Counting()
    server, port = _serve(state)
    try:
        code, _headers, _body = _post(port, "/api/config", {"path": "x"}, cookie=sid)
        assert code == 403, "an editor must be refused POST /api/config"
    finally:
        server.shutdown()
        server.server_close()

    assert reads.count("principal") == 1, reads
    assert "identity" not in reads, reads


def test_the_fastapi_gate_derives_identity_from_the_same_read_too(tmp_path: Path) -> None:
    """The same single-snapshot rule on the hosted backend. Both gates route through `gate.py`, so
    a divergence here is the failure BE-0253 put the policy there to prevent — and the human path
    is the one an earlier review found asserted for only one backend."""
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    assert state.repository is not None
    state.repository.upsert_user(
        "dana", org_id="acme", github_login="dana", email="dana@x", role="editor"
    )
    sid = state.auth.issue_session("dana")

    reads: list[str] = []
    inner = state.auth.sessions

    class _Counting:
        def __getattr__(self, name: str) -> Any:
            attribute = getattr(inner, name)
            if name not in ("principal", "identity", "valid"):
                return attribute

            def counted(*args: Any, **kwargs: Any) -> Any:
                reads.append(name)
                return attribute(*args, **kwargs)

            return counted

    state.auth.sessions = _Counting()
    client = TestClient(make_app(state))
    client.cookies.set("bajutsu_session", sid)
    assert client.post("/api/config", json={"path": "x"}).status_code == 403

    assert reads.count("principal") == 1, reads
    assert "identity" not in reads, reads


# --- unit 3: the org travels with the session ---------------------------------------------------


def _machine(state: ServeState, key: RSAKey, org: str, **claims: Any) -> str:
    """A live machine session for *org*, minted the way a pipeline's own exchange mints one."""
    _payload, status, sid = ops.oidc_exchange(state, _token(key, **claims), org)
    assert status == 200 and sid is not None
    return sid


def _audit_rows(state: ServeState) -> list[Any]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import AuditLog

    # Read straight off the engine: the audit table has no read seam on the `Repository` protocol,
    # so this reaches past it into the SQL implementation the tests wire.
    repository: Any = state.repository
    assert repository is not None
    with Session(repository._engine) as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.at)))


def _admin(state: ServeState) -> str:
    """An admin with a real `users` row, so the audit entry its actions write satisfies the
    `audit_log.actor_id` foreign key."""
    assert state.repository is not None
    state.repository.upsert_user(
        "alice", org_id="acme", github_login="alice", email="alice@x", role="admin"
    )
    return "alice"


def test_a_machine_upload_lands_in_its_own_org_never_default(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """`org_of` reads a persisted user row, and a pipeline has none — so without the org travelling
    on the session every allowlisted call would resolve `default`, whatever `allowedRepositories`
    said. A cross-tenant hole on exactly the routes the allowlist opens first."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    source = tmp_path / "app.zip"
    source.write_bytes(b"binary")
    payload, status = ops.bind_artifact(
        state,
        "binary",
        source,
        sha256="b" * 64,
        actor="repo:acme/app",
        machine_org="acme",
    )
    assert status == 200 and payload["ok"] is True
    # Stored under `acme`, so the probe finds it there and not in the fallback tenant.
    assert ops.artifact_exists(state, "binary", "b" * 64, machine_org="acme")[0] == {"exists": True}
    assert ops.artifact_exists(state, "binary", "b" * 64, machine_org="globex")[0] == (
        {"exists": False}
    )
    assert ops.artifact_exists(state, "binary", "b" * 64, actor=None)[0] == {"exists": False}


def test_the_audit_entry_names_the_repository_with_no_user_row_behind_it(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """`actor_id` is a foreign key to `users.id` and a machine has no row, so the entry is kept with
    a null actor and the repository recorded in its detail — "which pipeline did this" stays
    answerable without a synthetic user in the roster `/api/orgs` discloses."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    source = tmp_path / "app.zip"
    source.write_bytes(b"binary")
    ops.bind_artifact(
        state, "binary", source, sha256="c" * 64, actor="repo:acme/app", machine_org="acme"
    )
    ops.artifact_exists(state, "binary", "c" * 64, actor="repo:acme/app", machine_org="acme")

    rows = _audit_rows(state)
    # The probe audits too, so a pipeline that finds its build already stored still leaves a trace.
    assert [row.action for row in rows] == ["artifact:binary", "artifact:binary:exists"]
    for row in rows:
        assert row.org_id == "acme"
        assert row.actor_id is None, "a login with no users row must never reach the foreign key"
        assert row.detail["repository"] == "acme/app"


def test_a_human_audit_entry_is_unchanged(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The machine branch is keyed on the reserved identity form, so a person's entry still carries
    their login in `actor_id` and gains no `repository` key."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    actor = _admin(state)
    source = tmp_path / "app.zip"
    source.write_bytes(b"binary")
    ops.bind_artifact(state, "binary", source, sha256="d" * 64, actor=actor)

    (row,) = _audit_rows(state)
    assert row.actor_id == actor and "repository" not in row.detail


def test_a_job_belonging_to_another_org_reads_as_missing(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """404 rather than 403: a job id is opaque, so "forbidden" would confirm that this particular
    id exists — the one thing the refusal is there to withhold."""
    from bajutsu.serve.state import Job

    key = _key()
    state = _state(serve_engine, tmp_path, key)
    job = state.job_registry.register(Job(org="acme"))

    assert ops.job_view(state, job.id, machine_org="acme")[1] == 200
    payload, status = ops.job_view(state, job.id, machine_org="globex")
    assert status == 404 and payload == {"error": "no such job"}
    # Indistinguishable from a job that never existed at all.
    assert ops.job_view(state, "nope", machine_org="acme") == (payload, status)
    # A person is unscoped here, as before: their org is read from a row `set_active_org` rewrites,
    # while a job's org is frozen at dispatch, so scoping them would 404 a member on their own
    # in-flight run merely because they switched org.
    assert ops.job_view(state, job.id)[1] == 200


def test_a_machine_reads_its_own_orgs_runs(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The run history is org-scoped, and the machine's org is the one the exchange verified."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    assert state.repository is not None
    from bajutsu.serve.server.db import RunRecord

    for org, run_id in (("acme", "r-acme"), ("globex", "r-globex")):
        state.repository.ensure_org(org, slug=org, name=org)
        state.repository.record_run(
            RunRecord(id=run_id, org_id=org, status="done", ok=True, summary={"id": run_id})
        )

    payload, status = ops.runs_payload(state, machine_org="acme")
    assert status == 200
    assert [run["id"] for run in payload] == ["r-acme"]
    assert [run["id"] for run in ops.runs_payload(state, machine_org="globex")[0]] == ["r-globex"]


# --- unit 3: revoking a machine session ---------------------------------------------------------


def test_revoking_one_orgs_machine_sessions_leaves_anothers_alone(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """`shared/ci` is listed by two orgs, so both mint sessions under the identity
    `repo:shared/ci`. Revoking on identity alone would let one org's admin end the other's running
    pipelines, which is why the store's revocation is scoped by org."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    admin = _admin(state)
    globex = _machine(state, key, "globex", repository="shared/ci", jti="a")
    shared = _machine(state, key, "shared", repository="shared/ci", jti="b")

    payload, status = ops.revoke_machine_sessions(
        state, "globex", {"repository": "shared/ci"}, actor=admin
    )
    assert status == 200 and payload["sessionsRevoked"] == 1
    assert state.auth.valid_session(globex) is False
    assert state.auth.valid_session(shared) is True, "another org's pipelines must survive"


def test_revoking_without_a_repository_ends_every_machine_session_in_the_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The reach an admin wants when the roster itself is what went wrong."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    admin = _admin(state)
    app = _machine(state, key, "acme", jti="a")
    web = _machine(state, key, "acme", repository="acme/web", environment="production", jti="b")
    human = state.auth.issue_session(admin)

    payload, status = ops.revoke_machine_sessions(state, "acme", {}, actor=admin)
    assert status == 200 and payload["sessionsRevoked"] == 2
    assert not state.auth.valid_session(app) and not state.auth.valid_session(web)
    assert state.auth.valid_session(human), "a person's session is not a machine's"


def test_retiring_an_org_revokes_the_machine_sessions_bound_to_it(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """`revoke_identities` is driven by the `users` table and a pipeline has no row in it, so a
    retired org's machine sessions would otherwise keep acting as that tenant until they expired —
    the exact leak BE-0375 added that revocation to close."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    assert state.repository is not None
    admin = _admin(state)
    machine = _machine(state, key, "acme")

    payload, status = ops.delete_org(state, "acme", actor=admin)
    assert status == 200 and payload["sessionsRevoked"] >= 1
    assert state.auth.valid_session(machine) is False


def test_the_revocation_endpoint_refuses_an_unknown_org_and_a_malformed_body(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    admin = _admin(state)
    assert ops.revoke_machine_sessions(state, "nope", {}, actor=admin)[1] == 404
    assert ops.revoke_machine_sessions(state, "acme", {"repository": " "}, actor=admin)[1] == 400
    assert ops.revoke_machine_sessions(state, "acme", {"repository": 7}, actor=admin)[1] == 400


def test_the_revocation_endpoint_is_admin_gated_and_closed_to_a_machine(tmp_path: Path) -> None:
    """It lives under `/api/orgs/` so it inherits that prefix's admin gate, and the machine
    allowlist does not name it — a pipeline cannot revoke anybody's sessions, its own included."""
    from bajutsu.serve import authz

    path = "/api/orgs/acme/machine-sessions/revoke"
    assert authz.required_role("POST", path) == "admin"

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    sid = _machine(state, key, "acme")
    server, port = _serve(state)
    try:
        assert _post(port, path, {}, cookie=sid)[0] == 403
    finally:
        server.shutdown()
        server.server_close()


def test_a_deployment_with_no_token_refuses_the_exchange(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """Both backends skip the request gate with no token configured, so nothing would run the
    allowlist or carry the verified org — every later call would answer 200 while quietly acting as
    the `default` tenant. Refused outright, like an unconfigured audience or a missing database."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    state.auth.token = None
    payload, status, sid = ops.oidc_exchange(state, _token(key), "acme")
    assert status == 400 and sid is None
    assert "authenticated deployment" in payload["error"]


def test_a_machine_session_takes_no_per_session_binding_slot(tmp_path: Path) -> None:
    """BE-0393 sized the slot map for members, and a restore is a Git or bundle fetch paid once per
    session. One session per CI job would pay that fetch per job and evict members' slots, so both
    backends resolve a machine's session to None before it reaches `binding_for`."""
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    sid = _machine(state, key, "acme")

    client = TestClient(make_app(state))
    client.cookies.set("bajutsu_session", sid)
    assert client.get("/api/runs").status_code == 200

    server, port = _serve(state)
    try:
        assert _get(port, "/api/runs", cookie=sid)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
    # The slot map stayed empty on both backends: a machine reads the deployment's fallback.
    assert state.bindings == {}


def test_revoking_matches_the_roster_casing(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The roster admits `Acme/App` and `acme/app` alike, so an admin who types the casing their own
    roster uses must not revoke nothing — a silent no-op on the very duty this endpoint serves."""
    key = _key()
    state = _state(serve_engine, tmp_path, key)
    admin = _admin(state)
    sid = _machine(state, key, "acme")

    payload, status = ops.revoke_machine_sessions(
        state, "acme", {"repository": "Acme/App"}, actor=admin
    )
    assert status == 200 and payload["sessionsRevoked"] == 1
    assert state.auth.valid_session(sid) is False


def test_an_allowlisted_read_is_audited_even_if_the_session_expires_mid_request(
    tmp_path: Path,
) -> None:
    """The gate reads the principal once and admits the request, but `ctx.actor()` would read the
    store a *second* time. A machine session at the end of its short time-to-live can pass the first
    read and answer None to the second — and `machine_org`, captured at the gate, still carries the
    tenant, so the call would succeed while `_record_audit`'s `not actor` early return dropped its
    entry. A pipeline's request with no trace of it. Both backends carry the gate's own identity."""
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    sid = _machine(state, key, "acme")
    inner = state.auth.sessions

    class _ExpiringBetweenTheTwoReads:
        """Live to `principal` (the gate's read), gone to `identity` (the operation's)."""

        def __getattr__(self, name: str) -> Any:
            if name == "identity":
                return lambda _sid: None
            return getattr(inner, name)

    state.auth.sessions = _ExpiringBetweenTheTwoReads()
    probe = "/api/artifacts/exists?kind=binary&sha256=" + "e" * 64

    client = TestClient(make_app(state))
    client.cookies.set("bajutsu_session", sid)
    assert client.get(probe).status_code == 200

    server, port = _serve(state)
    try:
        assert _get(port, probe, cookie=sid)[0] == 200
    finally:
        server.shutdown()
        server.server_close()

    # One row per backend, each naming the repository the gate read rather than dropping the entry.
    rows = _audit_rows(state)
    assert len(rows) == 2, "both backends must audit the probe"
    for row in rows:
        assert row.org_id == "acme"
        assert row.actor_id is None
        assert row.detail["repository"] == "acme/app"


def test_a_human_revoked_mid_request_does_not_land_in_the_default_org(tmp_path: Path) -> None:
    """The same two-read window, on the caller shape where it costs more. A person revoked between
    the gate's read and the operation's resolves `org_of(None)` — `default`, not their own tenant —
    so the write lands in the wrong org *and* drops its audit row. Org retirement revoking an
    in-flight request is the realistic trigger, not a time-to-live expiring."""
    from fastapi.testclient import TestClient

    from bajutsu.serve.server.app import make_app

    key = _key()
    state = _state(_threaded(tmp_path), tmp_path, key)
    assert state.repository is not None
    state.repository.upsert_user(
        "alice", org_id="acme", github_login="alice", email="alice@x", role="admin"
    )
    sid = state.auth.issue_session("alice")
    inner = state.auth.sessions

    class _RevokedBetweenTheTwoReads:
        def __getattr__(self, name: str) -> Any:
            if name == "identity":
                return lambda _sid: None
            return getattr(inner, name)

    state.auth.sessions = _RevokedBetweenTheTwoReads()
    probe = "/api/artifacts/exists?kind=binary&sha256=" + "f" * 64

    client = TestClient(make_app(state))
    client.cookies.set("bajutsu_session", sid)
    assert client.get(probe).status_code == 200

    server, port = _serve(state)
    try:
        assert _get(port, probe, cookie=sid)[0] == 200
    finally:
        server.shutdown()
        server.server_close()

    rows = _audit_rows(state)
    assert len(rows) == 2, "both backends must audit the probe"
    for row in rows:
        assert row.org_id == "acme", "the caller's own tenant, never the default org"
        assert row.actor_id == "alice"

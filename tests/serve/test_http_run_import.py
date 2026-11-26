"""BE-0510: `POST /api/runs/import` on the wire, on both backends."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from _run_import import RUN_ID, minimal_files, zip_files
from _shared import _serve, queue_repository
from sqlalchemy import Engine

from bajutsu import serve as srv
from bajutsu.serve import handler as handler_mod
from bajutsu.serve import operations as ops
from bajutsu.serve.authz import required_role
from bajutsu.serve.server import app as app_mod
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import Base
from bajutsu.serve.state import SessionManager

_Post = Callable[[bytes], tuple[int, Any]]


def _body(tmp_path: Path) -> bytes:
    return zip_files(tmp_path / "run.zip", minimal_files()).read_bytes()


def _state(tmp_path: Path, **kwargs: Any) -> srv.ServeState:
    return srv.ServeState(
        scenarios_dir=tmp_path, config=None, runs_dir=tmp_path / "runs", cwd=tmp_path, **kwargs
    )


def _send(port: int, data: bytes, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/runs/import",
        data=data,
        headers={"Content-Type": "application/zip", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture(params=["stdlib", "fastapi"])
def post(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Post]:
    state = _state(tmp_path)
    if request.param == "stdlib":
        server, port = _serve(state)
        yield lambda data: _send(port, data)
        server.shutdown()
        return
    from fastapi.testclient import TestClient

    client = TestClient(app_mod.make_app(state), base_url="http://127.0.0.1")

    def send(data: bytes) -> tuple[int, Any]:
        r = client.post(
            "/api/runs/import", content=data, headers={"Content-Type": "application/zip"}
        )
        return r.status_code, r.json()

    yield send


def test_a_run_zip_is_imported_and_its_url_returned(post: _Post, tmp_path: Path) -> None:
    status, body = post(_body(tmp_path))

    assert status == 200
    assert body["url"] == f"/runs/{RUN_ID}/report.html"
    assert (tmp_path / "runs" / RUN_ID / "report.html").is_file()


def test_a_body_that_is_no_zip_gets_400(post: _Post, tmp_path: Path) -> None:
    status, body = post(b"not a zip")

    assert status == 400
    assert "not a valid zip" in body["error"]
    assert not (tmp_path / "runs" / RUN_ID).exists()


def test_a_body_over_the_import_cap_gets_413(
    post: _Post, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handler_mod, "MAX_RUN_IMPORT_BYTES", 8)
    monkeypatch.setattr(app_mod, "MAX_RUN_IMPORT_BYTES", 8)

    assert post(_body(tmp_path))[0] == 413


def test_the_shared_token_authorizes_an_import(tmp_path: Path) -> None:
    state = _state(tmp_path, auth=SessionManager(token="s3cret"))
    server, port = _serve(state)
    try:
        assert _send(port, _body(tmp_path))[0] == 401
        assert _send(port, _body(tmp_path), {"Authorization": "Bearer s3cret"})[0] == 200
    finally:
        server.shutdown()


def test_importing_a_run_needs_the_editor_role() -> None:
    assert required_role("POST", "/api/runs/import") == "editor"


@pytest.mark.parametrize(("role", "expected"), [("viewer", True), ("editor", False)])
def test_a_viewer_is_refused_the_import(
    serve_engine: Callable[..., Engine], tmp_path: Path, role: str, expected: bool
) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="default")
    repo.upsert_user("u", org_id="default", github_login="u", email="u@x", role=role)

    from bajutsu.serve.authz import forbidden_for_role

    state = _state(tmp_path, repository=repo)

    assert forbidden_for_role(state, "u", "POST", "/api/runs/import") is expected


@pytest.mark.parametrize("backend", ["stdlib", "fastapi"])
def test_both_backends_hand_the_signed_in_user_to_the_import(tmp_path: Path, backend: str) -> None:
    repo = queue_repository()  # one shared connection, which the server's own thread can read
    repo.ensure_org("default", slug="default", name="default")
    repo.ensure_org("acme", slug="acme", name="acme")
    repo.upsert_user("ann", org_id="acme", github_login="ann", email="n@x", role="editor")
    state = _state(tmp_path, repository=repo, auth=SessionManager(token="s3cret"))
    sid = state.auth.issue_session(identity="ann")
    cookie = f"bajutsu_session={sid}"

    if backend == "stdlib":
        server, port = _serve(state)
        try:
            status, _ = _send(port, _body(tmp_path), {"Cookie": cookie})
        finally:
            server.shutdown()
    else:
        from fastapi.testclient import TestClient

        client = TestClient(app_mod.make_app(state), base_url="http://127.0.0.1")
        status = client.post(
            "/api/runs/import", content=_body(tmp_path), headers={"Cookie": cookie}
        ).status_code

    assert status == 200
    row = repo.get_run(RUN_ID)
    assert row is not None
    assert (row.org_id, row.created_by) == ("acme", "ann")


def test_an_unexpected_failure_answers_500_with_a_json_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The raw-body route sits before do_POST's error boundary, so it needs its own (BE-0264).
    def boom(*_args: Any, **_kwargs: Any) -> tuple[Any, int]:
        raise RuntimeError("boom")

    monkeypatch.setattr(ops, "import_run", boom)
    server, port = _serve(_state(tmp_path))
    try:
        status, body = _send(port, _body(tmp_path))
    finally:
        server.shutdown()

    assert status == 500
    assert "error" in body

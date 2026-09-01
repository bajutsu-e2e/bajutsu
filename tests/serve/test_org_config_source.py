"""BE-0404 unit 1: the org holds its own config source, and a replica restores from it.

The project row a hosted replica used to read to recover an uploaded bundle it never received is
now one column on the org row, written by the bind itself. Without that move the recovery path
would have nothing to read: the record used to reach the database only when a client re-registered
it through `POST /api/projects`, the endpoint this item deletes.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path

from _shared import FakeObjectStore
from sqlalchemy import Engine

from bajutsu import serve as srv
from bajutsu.serve import operations as ops

_CONFIG = "targets:\n  docs: { baseUrl: 'https://example.test/', backend: [web] }\n"


def _bundle(tmp_path: Path) -> tuple[Path, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bajutsu.config.yaml", _CONFIG)
    blob = buf.getvalue()
    path = tmp_path / "bundle.zip"
    path.write_bytes(blob)
    return path, hashlib.sha256(blob).hexdigest()


def _state(serve_engine: Callable[..., Engine], tmp_path: Path, **extra: object) -> srv.ServeState:
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    repository = SqlRepository(engine)
    repository.ensure_org("acme", slug="acme", name="acme")
    repository.upsert_user("kazu", org_id="acme", github_login="kazu", email="k@x")
    return srv.ServeState(
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        repository=repository,
        uploads_dir=tmp_path / "uploads",
        **extra,  # type: ignore[arg-type]
    )


def test_binding_a_bundle_remembers_it_on_the_acting_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _state(serve_engine, tmp_path, object_store=FakeObjectStore())
    assert state.repository is not None
    zip_path, sha256 = _bundle(tmp_path)

    _, status = ops.bind_upload_config(state, zip_path, "bundle.zip", sha256=sha256, actor="kazu")
    assert status == 200

    org = state.repository.get_org("acme")
    assert org is not None
    assert org.config_source == {
        "kind": "upload",
        "filename": "bundle.zip",
        "sha256": sha256,
        "size": zip_path.stat().st_size,
    }
    # The record is the acting org's alone — no other org learns which bundle acme bound.
    state.repository.ensure_org("globex", slug="globex", name="globex")
    other = state.repository.get_org("globex")
    assert other is not None and other.config_source is None


def test_a_replica_restores_the_org_s_remembered_bundle(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The whole point of the column: a second process that never received the upload recovers it
    # from the object store by the digest the org row names.
    store = FakeObjectStore()
    first = _state(serve_engine, tmp_path, object_store=store)
    assert first.repository is not None
    zip_path, sha256 = _bundle(tmp_path)
    assert ops.bind_upload_config(first, zip_path, "b.zip", sha256=sha256, actor="kazu")[1] == 200

    replica = srv.ServeState(
        runs_dir=tmp_path / "runs2",
        cwd=tmp_path,
        repository=first.repository,
        uploads_dir=tmp_path / "uploads2",  # a cold cache: the bytes must come from the store
        object_store=store,
    )
    assert replica.config is None
    result, status = ops.restore_org_config(replica, org="acme", actor="kazu")
    assert status == 200
    assert replica.config is not None
    assert replica.config.read_text(encoding="utf-8") == _CONFIG
    assert result["ok"] is True


def test_restoring_without_a_remembered_bundle_is_a_clean_404(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _state(serve_engine, tmp_path, object_store=FakeObjectStore())
    assert ops.restore_org_config(state, org="acme")[1] == 404


def test_restoring_needs_a_database(tmp_path: Path) -> None:
    # A database-less serve keeps no config memory at all — say so rather than answering "nothing
    # remembered", which would read as "you have not bound anything yet".
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    assert ops.restore_org_config(state, org="default")[1] == 400


def test_a_second_bind_replaces_the_first_locator(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # One record per org, not a list of named bindings — the deliberate loss this item accepts, so
    # the named-project layer does not return under another name.
    state = _state(serve_engine, tmp_path, object_store=FakeObjectStore())
    assert state.repository is not None
    first, first_sha = _bundle(tmp_path)
    assert ops.bind_upload_config(state, first, "one.zip", sha256=first_sha, actor="kazu")[1] == 200

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bajutsu.config.yaml", _CONFIG + "  extra: { baseUrl: 'https://x.test/' }\n")
    blob = buf.getvalue()
    second = tmp_path / "second.zip"
    second.write_bytes(blob)
    second_sha = hashlib.sha256(blob).hexdigest()
    assert (
        ops.bind_upload_config(state, second, "two.zip", sha256=second_sha, actor="kazu")[1] == 200
    )

    org = state.repository.get_org("acme")
    assert org is not None and org.config_source is not None
    assert org.config_source["sha256"] == second_sha

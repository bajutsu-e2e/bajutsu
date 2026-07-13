"""The hosted `SqlRepository` run soft-delete / restore / purge (BE-0239).

The hosted regular-run history is DB-driven (`Repository.list_runs`), so a soft-delete there is a
`deleted_at`/`deleted_by` column update that the listing filters on — the DB counterpart to the
object store's tombstone. Purge deletes the row outright. Exercised on in-memory SQLite, org-scoped.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    for org in ("acme", "globex"):
        repo.ensure_org(org, slug=org, name=org)
    return repo


def _run(repo: SqlRepository, run_id: str, org: str = "acme") -> None:
    repo.record_run(RunRecord(id=run_id, org_id=org, status="done", ok=True))


def _ids(repo: SqlRepository, org: str = "acme", **kw) -> list[str]:
    return [r.id for r in repo.list_runs(org_id=org, **kw)]


def test_soft_delete_hides_a_run_from_list_runs() -> None:
    repo = _repo()
    _run(repo, "r1")
    _run(repo, "r2")
    assert (
        repo.soft_delete_run("r1", org_id="acme", deleted_by="alice", at=datetime.now(UTC)) is True
    )
    assert _ids(repo) == ["r2"]
    assert "r1" in _ids(repo, include_deleted=True)  # still there, just filtered out


def test_soft_delete_is_org_scoped() -> None:
    repo = _repo()
    _run(repo, "r1", org="acme")
    # globex cannot trash acme's run — no matching row, a clean not-found.
    assert (
        repo.soft_delete_run("r1", org_id="globex", deleted_by="bob", at=datetime.now(UTC)) is False
    )
    assert _ids(repo, org="acme") == ["r1"]


def test_soft_delete_twice_is_false_the_second_time() -> None:
    repo = _repo()
    _run(repo, "r1")
    assert (
        repo.soft_delete_run("r1", org_id="acme", deleted_by="alice", at=datetime.now(UTC)) is True
    )
    assert (
        repo.soft_delete_run("r1", org_id="acme", deleted_by="alice", at=datetime.now(UTC)) is False
    )


def test_restore_brings_a_run_back() -> None:
    repo = _repo()
    _run(repo, "r1")
    repo.soft_delete_run("r1", org_id="acme", deleted_by="alice", at=datetime.now(UTC))
    assert repo.restore_run("r1", org_id="acme") is True
    assert _ids(repo) == ["r1"]
    assert repo.restore_run("r1", org_id="acme") is False  # nothing trashed now


def test_record_run_does_not_resurrect_or_clobber_the_marker() -> None:
    # A status update (re-`record_run`) on a trashed run must not clear its soft-delete marker.
    repo = _repo()
    _run(repo, "r1")
    repo.soft_delete_run("r1", org_id="acme", deleted_by="alice", at=datetime.now(UTC))
    repo.record_run(RunRecord(id="r1", org_id="acme", status="done", ok=False))
    assert _ids(repo) == []  # still hidden
    assert repo.get_run("r1").deleted_at is not None


def test_purge_deletes_the_row() -> None:
    repo = _repo()
    _run(repo, "r1")
    repo.soft_delete_run("r1", org_id="acme", deleted_by="alice", at=datetime.now(UTC))
    assert repo.purge_run("r1", org_id="acme") is True
    assert repo.get_run("r1") is None
    assert repo.purge_run("r1", org_id="acme") is False  # already gone


def test_purge_is_org_scoped() -> None:
    repo = _repo()
    _run(repo, "r1", org="acme")
    assert repo.purge_run("r1", org_id="globex") is False  # another org can't purge it
    assert repo.get_run("r1") is not None

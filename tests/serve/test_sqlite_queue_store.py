"""The default SQLite queue store is safe under concurrent writers (BE-0449 unit 1).

A worker reaches the server over HTTP and never opens the database, so the concurrent writers are
the server's own request threads. SQLite has no `FOR UPDATE SKIP LOCKED`, so each job transition is
one conditional UPDATE whose row count decides the race. These tests run on a file-backed SQLite —
the store a local start uses — with real threads, and pin the engine settings and the automatic
migration of the default database.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.orm import Session

from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db._functions import engine_from_url, repository_from_env
from bajutsu.serve.server.models import Base, JobRecord


def _file_repo(tmp_path: Path) -> SqlRepository:
    engine = engine_from_url(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _backdate(repo: SqlRepository, job_id: str, seconds: float) -> None:
    with Session(repo._engine) as session:
        row = session.get(JobRecord, job_id)
        assert row is not None
        row.leased_at = datetime.now(UTC) - timedelta(seconds=seconds)
        session.commit()


@contextmanager
def _contend_at_job_update(engine: Engine, parties: int) -> Iterator[None]:
    """Hold the first *parties* `UPDATE jobs` statements until all of them are about to run.

    Every contender has then finished whatever it read before writing, so the writes collide on the
    same row: a select-then-write store would let each overwrite the other, while a conditional
    UPDATE lets exactly one match. One-shot, so the statements after the collision run freely.
    """
    barrier = threading.Barrier(parties, timeout=10)
    held = 0
    lock = threading.Lock()

    def hold(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        nonlocal held
        if not statement.lstrip().upper().startswith("UPDATE JOBS"):
            return
        with lock:
            if held >= parties:
                return
            held += 1
        barrier.wait()

    event.listen(engine, "before_cursor_execute", hold)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", hold)


def test_concurrent_leasers_never_take_one_job_twice(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    leasers = 4
    leased: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def lease(worker: str) -> None:
        try:
            job = repo.lease_job(worker)
        except Exception as e:  # surfaced below: a thread's error must fail the test
            errors.append(e)
            return
        if job is not None:
            with lock:
                leased.append(worker)

    with _contend_at_job_update(repo._engine, leasers):
        threads = [threading.Thread(target=lease, args=(f"w{i}",)) for i in range(leasers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            assert not t.is_alive()
    assert errors == []  # no "database is locked" under contention
    assert len(leased) == 1  # every leaser reached the claim on the one job; exactly one won it
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased" and info["leased_by"] == leased[0]


def test_a_heartbeat_after_a_reclaim_does_not_resurrect_the_lease(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    _backdate(repo, "j1", 300)
    assert repo.reclaim_expired_leases(timedelta(seconds=120)) == ["j1"]
    assert repo.heartbeat_job("j1", "w1") is False
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "queued" and info["leased_by"] is None


def test_a_reclaim_after_a_heartbeat_leaves_the_live_lease(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate(repo, "j1", 300)
    assert repo.heartbeat_job("j1", "w1") is True
    assert repo.reclaim_expired_leases(timedelta(seconds=120)) == []
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased" and info["leased_by"] == "w1"


def _race_once(path: Path) -> None:
    engine = engine_from_url(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate(repo, "j1", 300)
    outcome: dict[str, object] = {}

    def beat() -> None:
        outcome["beat"] = repo.heartbeat_job("j1", "w1")

    def reclaim() -> None:
        outcome["reclaimed"] = repo.reclaim_expired_leases(timedelta(seconds=120))

    # Both writes are held until each side has read the leased, expired row, so they collide.
    with _contend_at_job_update(engine, 2):
        threads = [threading.Thread(target=beat), threading.Thread(target=reclaim)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            assert not t.is_alive()
    # A thread that raised never wrote its key, so the run fails here rather than passing quietly.
    assert set(outcome) == {"beat", "reclaimed"}
    info = repo.get_job("j1")
    assert info is not None
    if outcome["beat"]:
        assert info["status"] == "leased" and outcome["reclaimed"] == []
    else:
        assert info["status"] == "queued" and outcome["reclaimed"] == ["j1"]
    engine.dispose()


def test_heartbeat_and_reclaim_racing_agree_on_one_outcome(tmp_path: Path) -> None:
    # Whichever lands first wins; the job is never both renewed and requeued.
    for i in range(10):
        _race_once(tmp_path / f"race-{i}.db")


def test_a_result_from_a_worker_that_lost_the_lease_is_refused(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate(repo, "j1", 300)
    repo.reclaim_expired_leases(timedelta(seconds=120))
    repo.lease_job("w2")
    assert repo.complete_job("j1", {"ok": True}, worker_id="w1") is False
    assert repo.complete_job("j1", {"ok": True}, worker_id="w2") is True
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "done"


def test_the_attempt_cap_fails_a_poison_job(tmp_path: Path) -> None:
    repo = SqlRepository(_file_repo(tmp_path)._engine, max_attempts=1)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate(repo, "j1", 300)
    assert repo.reclaim_expired_leases(timedelta(seconds=120), max_attempts=1) == []
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "failed"
    assert info["result"] == {"error": "lease expired after 1 attempts"}


def test_a_file_backed_sqlite_uses_wal_and_a_busy_timeout(tmp_path: Path) -> None:
    engine = engine_from_url(f"sqlite:///{tmp_path / 'q.db'}")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        # Not the driver's own 5000 default, so this pins the setting rather than the default.
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30_000


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_an_in_memory_sqlite_is_left_alone(url: str) -> None:
    with engine_from_url(url).connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "memory"


def test_the_default_database_is_migrated_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path / 'default.db'}"
    repo = repository_from_env(default_url=url)
    assert repo is not None
    tables = set(inspect(repo._engine).get_table_names())
    assert {"jobs", "runs", "alembic_version"} <= tables
    # Idempotent: a second start finds it at head and changes nothing.
    assert repository_from_env(default_url=url) is not None


def test_a_configured_database_wins_and_is_never_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = f"sqlite:///{tmp_path / 'configured.db'}"
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", configured)
    repo = repository_from_env(default_url=f"sqlite:///{tmp_path / 'default.db'}")
    assert repo is not None
    assert inspect(repo._engine).get_table_names() == []  # the operator owns its schema
    assert not (tmp_path / "default.db").exists()


def test_the_explicit_url_is_migrated_even_with_the_environment_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.server.db._functions import upgrade_to_head

    configured = tmp_path / "configured.db"
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", f"sqlite:///{configured}")
    upgrade_to_head(f"sqlite:///{tmp_path / 'default.db'}")
    assert (tmp_path / "default.db").exists()
    assert not configured.exists()


def test_no_database_without_a_url_or_a_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    assert repository_from_env() is None

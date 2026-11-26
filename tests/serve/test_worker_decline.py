"""A worker gives back a job whose devices it could not borrow (BE-0511)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository, repository_from_env
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base, JobRecord


@pytest.fixture
def repo(serve_engine: Callable[..., Engine]) -> SqlRepository:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _state(repo: SqlRepository, tmp_path: Path) -> srv.ServeState:
    return srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )


def _enqueue(repo: SqlRepository, job_id: str) -> None:
    repo.enqueue_job(job_id, org_id="o1", spec={"cmd": [], "job_id": job_id})


def _row(repo: SqlRepository, job_id: str) -> JobRecord:
    with Session(repo._engine) as session:
        row = session.get(JobRecord, job_id)
        assert row is not None
        return row


def _set(repo: SqlRepository, job_id: str, **values: object) -> None:
    with Session(repo._engine) as session:
        session.execute(update(JobRecord).where(JobRecord.id == job_id).values(**values))
        session.commit()


def _decline(
    state: srv.ServeState, job_id: str, worker: str = "w1"
) -> tuple[dict[str, object], int]:
    return ops.worker_decline(
        state,
        {"worker_id": worker, "job_id": job_id, "reason": "no device", "environment": "leased"},
    )


def _lease(
    repo: SqlRepository, environment: str | None = "leased", worker: str = "w1"
) -> list[str]:
    return [j.id for j in repo.lease_jobs(worker, environment=environment)]


def test_a_declined_job_returns_to_the_queue_without_spending_an_attempt(
    repo: SqlRepository, tmp_path: Path
) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    assert _lease(repo) == ["j1"]
    assert _decline(state, "j1") == ({"status": "queued"}, 200)
    row = _row(repo, "j1")
    assert (row.status, row.leased_by, row.attempts) == ("queued", None, 0)
    assert (row.decline_count, row.decline_reason, row.declined_environment) == (
        1,
        "no device",
        "leased",
    )


def test_only_the_declining_environment_waits_and_younger_jobs_run(
    repo: SqlRepository, tmp_path: Path
) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    _enqueue(repo, "j2")
    assert _lease(repo) == ["j1"]
    _decline(state, "j1")
    # The leased worker moves on to the younger job; a local one takes the older at once.
    assert _lease(repo) == ["j2"]
    assert _lease(repo, "local", "w2") == ["j1"]


def test_the_delay_doubles_and_stops_at_a_minute(repo: SqlRepository, tmp_path: Path) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    delays = []
    for _ in range(8):
        _set(repo, "j1", not_before=None)
        assert _lease(repo) == ["j1"]
        before = datetime.now(UTC)
        _decline(state, "j1")
        not_before = _row(repo, "j1").not_before
        assert not_before is not None
        # Floored: `not_before` counts from a clock read just after `before`.
        delays.append(int((not_before.replace(tzinfo=UTC) - before).total_seconds()))
    assert delays == [2, 4, 8, 16, 32, 60, 60, 60]
    assert _lease(repo) == []  # still waiting out the last delay


def test_a_job_fails_once_the_decline_window_has_passed(
    repo: SqlRepository, tmp_path: Path
) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    assert _lease(repo) == ["j1"]
    _decline(state, "j1")
    _set(repo, "j1", not_before=None, first_declined_at=datetime.now(UTC) - timedelta(hours=2))
    assert _lease(repo) == ["j1"]
    assert _decline(state, "j1") == ({"status": "failed"}, 200)
    row = _row(repo, "j1")
    assert row.status == "failed"
    assert row.result["error"] == "no device was lent within 3600 seconds: no device"


def test_a_cancelled_job_is_not_queued_again(repo: SqlRepository, tmp_path: Path) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    assert _lease(repo) == ["j1"]
    repo.cancel_job("j1")
    assert _decline(state, "j1") == ({"status": "cancelled"}, 200)
    assert _row(repo, "j1").status == "cancelled"


def test_one_worker_cannot_decline_anothers_job(repo: SqlRepository, tmp_path: Path) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    assert _lease(repo) == ["j1"]
    assert _decline(state, "j1", worker="other")[1] == 409
    assert repo.decline_job("j1", "other", reason="x", environment="leased") is None
    assert _row(repo, "j1").status == "leased"


@pytest.mark.parametrize("missing", ["reason", "environment", "job_id", "worker_id"])
def test_a_decline_names_everything(repo: SqlRepository, tmp_path: Path, missing: str) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    assert _lease(repo) == ["j1"]
    body = {"worker_id": "w1", "job_id": "j1", "reason": "r", "environment": "leased"}
    del body[missing]
    assert ops.worker_decline(state, body)[1] == 400


def test_the_window_is_a_server_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_DECLINE_WINDOW_SECONDS", "90")
    built = repository_from_env()
    assert built is not None
    assert built._decline_window == timedelta(seconds=90)
    monkeypatch.setenv("BAJUTSU_DECLINE_WINDOW_SECONDS", "never")
    with pytest.raises(ValueError, match="BAJUTSU_DECLINE_WINDOW_SECONDS"):
        repository_from_env()


def test_a_failed_decline_closes_the_log_stream_with_the_reason(
    repo: SqlRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    assert _lease(repo) == ["j1"]
    _set(repo, "j1", first_declined_at=datetime.now(UTC) - timedelta(hours=2))
    published: list[str] = []
    closed: list[str] = []
    monkeypatch.setattr(state.logbus, "publish", lambda _job, line: published.append(line))
    monkeypatch.setattr(state.logbus, "close", lambda _job, payload="": closed.append(payload))
    assert _decline(state, "j1")[0] == {"status": "failed"}
    assert published == ["error: no device was lent within 3600 seconds: no device"]
    assert len(closed) == 1


def test_a_lease_lost_between_the_check_and_the_decline_is_a_409(
    repo: SqlRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(repo, tmp_path)
    _enqueue(repo, "j1")
    assert _lease(repo) == ["j1"]
    monkeypatch.setattr(repo, "decline_job", lambda *_a, **_k: None)
    assert _decline(state, "j1")[1] == 409

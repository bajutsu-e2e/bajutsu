"""The `Repository` seam (BE-0015 7a): the run round-trip, org-scoped listing, and the env-driven
factory, all against an in-memory SQLite database built inside each test (no live Postgres). Only
the `runs` methods exist in 7a; orgs/users/projects/audit_log arrive with 7b/7c."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from bajutsu.serve.server.db import RunRecord, SqlRepository, engine_from_url, repository_from_env
from bajutsu.serve.server.models import Base


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def test_record_then_get_round_trips() -> None:
    repo = _repo()
    repo.record_run(
        RunRecord(id="r1", org_id="o1", status="done", ok=True, summary={"passed": 3, "failed": 0})
    )
    got = repo.get_run("r1")
    assert got is not None
    assert got.id == "r1"
    assert got.org_id == "o1"
    assert got.status == "done"
    assert got.ok is True
    assert got.summary == {"passed": 3, "failed": 0}


def test_get_missing_returns_none() -> None:
    assert _repo().get_run("nope") is None


def test_list_runs_filters_by_org_and_orders_newest_first() -> None:
    repo = _repo()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    repo.record_run(RunRecord(id="a", org_id="o1", status="done", created_at=base.replace(hour=1)))
    repo.record_run(RunRecord(id="b", org_id="o1", status="done", created_at=base.replace(hour=3)))
    repo.record_run(RunRecord(id="c", org_id="o2", status="done", created_at=base.replace(hour=2)))
    assert [r.id for r in repo.list_runs(org_id="o1")] == ["b", "a"]


def test_list_runs_respects_limit() -> None:
    repo = _repo()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        repo.record_run(
            RunRecord(id=f"r{i}", org_id="o1", status="done", created_at=base.replace(minute=i))
        )
    assert len(repo.list_runs(org_id="o1", limit=2)) == 2


def test_summary_json_roundtrips_a_nested_value() -> None:
    repo = _repo()
    summary = {"counts": {"passed": 2, "failed": 1}, "steps": [{"name": "tap", "ok": True}]}
    repo.record_run(RunRecord(id="r1", org_id="o1", status="done", summary=summary))
    got = repo.get_run("r1")
    assert got is not None
    assert got.summary == summary


def test_record_run_is_idempotent_by_id() -> None:
    repo = _repo()
    repo.record_run(RunRecord(id="r1", org_id="o1", status="running"))
    repo.record_run(RunRecord(id="r1", org_id="o1", status="done", ok=True))
    got = repo.get_run("r1")
    assert got is not None
    assert got.status == "done"
    assert got.ok is True
    assert len(repo.list_runs(org_id="o1")) == 1


def test_engine_from_url_builds_a_usable_engine() -> None:
    engine = engine_from_url("sqlite://")
    Base.metadata.create_all(engine)
    assert SqlRepository(engine).get_run("absent") is None


def test_repository_from_env_is_none_without_a_url(monkeypatch) -> None:
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    assert repository_from_env() is None


def test_repository_from_env_builds_a_sql_repository(monkeypatch) -> None:
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    assert isinstance(repository_from_env(), SqlRepository)

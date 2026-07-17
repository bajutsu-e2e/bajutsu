"""The run-lifecycle serve operations: delete / restore / bulk-delete / retention sweep (BE-0239).

Covers both deployment shapes: local (no repository — the artifact store's trash is the whole
story) and hosted (a repository whose `deleted_at` column drives the DB-backed listing, updated
alongside the store). Also the purge admin gate the path-based RBAC can't apply, org scoping, the
audit-log entry, and the lazy retention sweep with an injected clock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select

from bajutsu.serve import operations as ops
from bajutsu.serve.artifacts import LocalArtifactStore
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import AuditLog, Base
from bajutsu.serve.state import ServeState


def _local_state(tmp_path: Path) -> ServeState:
    return ServeState(runs_dir=tmp_path / "runs")


def _hosted_state(tmp_path: Path) -> tuple[ServeState, SqlRepository]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="default")
    repo.upsert_user("admin", org_id="default", github_login="admin", email="a@x", role="admin")
    repo.upsert_user("editor", org_id="default", github_login="editor", email="e@x", role="editor")
    state = ServeState(runs_dir=tmp_path / "runs", repository=repo)
    return state, repo


def _run_dir(state: ServeState, run_id: str) -> None:
    d = state.runs_dir / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text('{"ok": true, "scenarios": []}')


def _run_dir_for(state: ServeState, run_id: str, *scenario_names: str) -> None:
    d = state.runs_dir / run_id
    d.mkdir(parents=True)
    scenarios = [{"scenario": name, "ok": True} for name in scenario_names]
    (d / "manifest.json").write_text(
        json.dumps({"ok": True, "scenarios": scenarios}), encoding="utf-8"
    )


def _audit_actions(repo: SqlRepository) -> list[tuple[str, str]]:
    with repo._engine.connect() as conn:
        rows = conn.execute(select(AuditLog.action, AuditLog.target)).all()
    return [(str(r[0]), str(r[1])) for r in rows]


# --- local (no repository) ---


def test_local_soft_delete_then_restore(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    assert ops.delete_run(state, "r1")[1] == 200
    assert ops.runs_payload(state)[0] == []  # delisted
    assert ops.restore_run(state, "r1")[1] == 200
    assert [r["id"] for r in ops.runs_payload(state)[0]] == ["r1"]


def test_local_delete_missing_run_is_404(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    assert ops.delete_run(state, "ghost")[1] == 404
    assert ops.restore_run(state, "ghost")[1] == 404


def test_local_purge_is_allowed_without_a_repository(tmp_path: Path) -> None:
    # Local serve has no RBAC (no repository) — purge is full-access, like every other action.
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    payload, status = ops.delete_run(state, "r1", purge=True)
    assert status == 200 and payload["purged"] is True
    assert not (state.runs_dir / "r1").exists()


def test_bulk_delete_reports_deleted_and_not_found(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    _run_dir(state, "r2")
    payload, status = ops.bulk_delete_runs(state, {"ids": ["r1", "r2", "ghost"]})
    assert status == 200
    assert set(payload["deleted"]) == {"r1", "r2"}
    assert payload["notFound"] == ["ghost"]
    assert ops.runs_payload(state)[0] == []


def test_bulk_delete_rejects_a_non_list_ids(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    assert ops.bulk_delete_runs(state, {"ids": "r1"})[1] == 400


def test_bulk_delete_purge_is_a_strict_boolean(tmp_path: Path) -> None:
    # A stringy "false" must NOT trigger an irreversible purge — only a JSON `true` does. The run is
    # soft-deleted (recoverable), not purged.
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    payload, status = ops.bulk_delete_runs(state, {"ids": ["r1"], "purge": "false"})
    assert status == 200 and payload["purged"] is False
    assert (
        state.artifacts.restore_run("r1") is True
    )  # still in the trash — soft-deleted, not purged


# --- trashed-runs listing (the Trash view, BE-0239 unit 5) ---


def test_trashed_runs_payload_lists_soft_deleted_only(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    _run_dir(state, "r2")
    ops.delete_run(state, "r1")  # trashed; r2 stays live
    listed, status = ops.trashed_runs_payload(state)
    assert status == 200
    assert [r["id"] for r in listed] == ["r1"]
    assert listed[0]["deletedAt"]  # a timestamp is stamped
    assert ops.runs_payload(state)[0][0]["id"] == "r2"  # the live list is the complement


def test_trashed_runs_payload_sweeps_expired_before_listing(tmp_path: Path) -> None:
    # The Trash view never offers an already-expired run as restorable: the lazy sweep runs first, so
    # a run past the retention window is purged (and gone from the list), matching runs_payload.
    state = _local_state(tmp_path)
    state.run_retention_days = 30
    _run_dir(state, "r1")
    ops.delete_run(state, "r1")
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    (state.runs_dir / ".trash" / "r1" / ".deleted").write_text(old, encoding="utf-8")
    assert ops.trashed_runs_payload(state)[0] == []


def test_trashed_runs_payload_is_org_scoped(tmp_path: Path) -> None:
    # A soft-deleted run in one org's store is invisible to another org's Trash view (BE-0015 holds
    # for the trash listing too): the payload reads the actor's org-scoped store, not the default one.
    # Route each org to its own runs dir through `org_stores`, as a server backend does with prefixes.
    from bajutsu.serve.state import StoreBundle

    state, repo = _hosted_state(tmp_path)
    repo.ensure_org("other", slug="other", name="other")
    repo.upsert_user("ed2", org_id="other", github_login="ed2", email="e2@x", role="editor")
    bundles = {
        org: StoreBundle(
            artifacts=LocalArtifactStore(tmp_path / org / "runs"),
            scenarios=state.scenarios,
            baselines=state.baselines,
            secrets=state.secrets,
            provider_settings=state.providers.store,
        )
        for org in ("default", "other")
    }
    state.org_stores = lambda org: bundles[org]
    repo.record_run(RunRecord(id="r1", org_id="default", status="done", ok=True))
    (tmp_path / "default" / "runs" / "r1").mkdir(parents=True)
    (tmp_path / "default" / "runs" / "r1" / "manifest.json").write_text('{"ok": true}')
    ops.delete_run(state, "r1", actor="editor")
    assert [r["id"] for r in ops.trashed_runs_payload(state, actor="editor")[0]] == ["r1"]
    assert ops.trashed_runs_payload(state, actor="ed2")[0] == []  # other org sees nothing


# --- scenario-scoped listing (BE-0262) ---


def test_runs_payload_unscoped_lists_every_run(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    _run_dir_for(state, "r1", "login")
    _run_dir_for(state, "r2", "checkout")
    assert {r["id"] for r in ops.runs_payload(state)[0]} == {"r1", "r2"}


def test_runs_payload_scoped_excludes_other_scenarios(tmp_path: Path) -> None:
    # BE-0262 Unit 1: the Author run picker scopes to the loaded scenario so a chosen run's step ids
    # line up with it. A run for another scenario is excluded.
    state = _local_state(tmp_path)
    _run_dir_for(state, "r1", "login")
    _run_dir_for(state, "r2", "checkout")
    assert [r["id"] for r in ops.runs_payload(state, scenario="login")[0]] == ["r1"]


def test_runs_payload_scoped_keeps_a_multi_scenario_run(tmp_path: Path) -> None:
    # A run that executed several scenarios matches when any of them is the loaded one.
    state = _local_state(tmp_path)
    _run_dir_for(state, "r1", "login", "checkout")
    assert [r["id"] for r in ops.runs_payload(state, scenario="checkout")[0]] == ["r1"]


def test_runs_payload_scoped_surfaces_a_run_past_the_hosted_cap(tmp_path: Path) -> None:
    # BE-0262 follow-up: the DB `list_runs` caps at the newest 50 runs. When scoping to a scenario,
    # that cap must count *scoped* runs — otherwise a run of the loaded scenario that falls outside
    # the newest-50 global window is silently dropped and the picker can't reach it.
    state, repo = _hosted_state(tmp_path)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    repo.record_run(
        RunRecord(
            id="login-run",
            org_id="default",
            status="done",
            ok=True,
            summary={"id": "login-run", "scenarios": ["login"]},
            created_at=base,  # the oldest run — past the newest-50 window below
        )
    )
    for i in range(50):
        repo.record_run(
            RunRecord(
                id=f"other-{i}",
                org_id="default",
                status="done",
                ok=True,
                summary={"id": f"other-{i}", "scenarios": ["checkout"]},
                created_at=base + timedelta(minutes=i + 1),
            )
        )
    scoped = ops.runs_payload(state, scenario="login", actor="admin")[0]
    assert [r["id"] for r in scoped] == ["login-run"]


def test_runs_payload_scoped_local_list_is_not_re_capped(tmp_path: Path) -> None:
    # BE-0262 follow-up: the local artifact-store listing is unbounded (no `list_runs` cap), so the
    # scenario-scoped list must stay unbounded too — re-capping it would make the local scoped picker
    # *stricter* than the unscoped one. All 51 runs of the loaded scenario must be reachable.
    state = _local_state(tmp_path)
    for i in range(51):
        _run_dir_for(state, f"login-{i:03d}", "login")
    scoped = ops.runs_payload(state, scenario="login")[0]
    assert len(scoped) == 51


# --- hosted (repository) ---


def test_hosted_soft_delete_updates_store_and_db(tmp_path: Path) -> None:
    state, repo = _hosted_state(tmp_path)
    repo.record_run(RunRecord(id="r1", org_id="default", status="done", ok=True))
    _run_dir(state, "r1")  # a regular run has both a DB row and store bytes
    assert ops.delete_run(state, "r1", actor="editor")[1] == 200
    assert repo.list_runs(org_id="default") == []  # DB listing hides it
    assert ("run.soft_delete", "r1") in _audit_actions(repo)


def test_hosted_purge_requires_admin(tmp_path: Path) -> None:
    state, repo = _hosted_state(tmp_path)
    repo.record_run(RunRecord(id="r1", org_id="default", status="done", ok=True))
    _run_dir(state, "r1")
    # An editor may soft-delete but not purge.
    assert ops.delete_run(state, "r1", purge=True, actor="editor")[1] == 403
    assert ops.delete_run(state, "r1", purge=True, actor="admin")[1] == 200
    assert repo.get_run("r1") is None


def test_hosted_bulk_purge_requires_admin(tmp_path: Path) -> None:
    state, _repo = _hosted_state(tmp_path)
    assert ops.bulk_delete_runs(state, {"ids": [], "purge": True}, actor="editor")[1] == 403
    assert ops.bulk_delete_runs(state, {"ids": [], "purge": True}, actor="admin")[1] == 200


# --- retention sweep ---


def test_sweep_purges_only_runs_past_the_window(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    state.run_retention_days = 30
    _run_dir(state, "r1")
    ops.delete_run(state, "r1")  # trashed ~now
    # Nothing is old enough yet, so the sweep keeps it.
    assert ops.sweep_expired_trash(state, now=datetime.now(UTC)) == 0
    assert state.artifacts.list_trashed_runs() != []
    # 40 days later it is past the 30-day window and gets purged.
    purged = ops.sweep_expired_trash(state, now=datetime.now(UTC) + timedelta(days=40))
    assert purged == 1
    assert state.artifacts.list_trashed_runs() == []


def test_sweep_is_a_no_op_when_retention_is_disabled(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    state.run_retention_days = 0  # disabled — trash kept until a manual purge
    _run_dir(state, "r1")
    ops.delete_run(state, "r1")
    assert ops.sweep_expired_trash(state, now=datetime.now(UTC) + timedelta(days=999)) == 0
    assert state.artifacts.list_trashed_runs() != []


def test_runs_payload_sweeps_before_listing(tmp_path: Path) -> None:
    # The lazy trigger (BE-0239): a history read purges expired trash first. Backdate the deletion
    # marker (the retention clock's start) past the window so the next read purges it.
    state = _local_state(tmp_path)
    state.run_retention_days = 30
    _run_dir(state, "r1")
    ops.delete_run(state, "r1")
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    (state.runs_dir / ".trash" / "r1" / ".deleted").write_text(old, encoding="utf-8")
    assert ops.runs_payload(state)[0] == []  # listed empty, and the sweep purged the expired trash
    assert state.artifacts.list_trashed_runs() == []


class _FakeObjectStore:
    """A mutable in-memory ObjectStore slice, so the sweep's object-store branch is driven
    end-to-end (not just the local-filesystem fallback the other hosted tests use)."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._o = dict(objects or {})

    def exists(self, key: str) -> bool:
        return key in self._o

    def get_bytes(self, key: str) -> bytes | None:
        return self._o.get(key)

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self._o[key] = data

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self._o if k.startswith(prefix)]

    def delete_key(self, key: str) -> None:
        self._o.pop(key, None)

    def delete_keys(self, keys) -> None:
        for k in list(keys):
            self._o.pop(k, None)


def test_sweep_end_to_end_on_the_object_store_backend(tmp_path: Path) -> None:
    # Drives the sweep's *object-store* branch (the real hosted/R2 target) end-to-end through
    # ops.delete_run + sweep_expired_trash with a repository — not the local-filesystem fallback.
    from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
    from bajutsu.serve.state import StoreBundle

    prefix = "artifacts/default/"
    fake = _FakeObjectStore({f"{prefix}r1/manifest.json": b'{"ok": true, "scenarios": []}'})
    art = ObjectStorageArtifactStore(fake, prefix=prefix)
    state, repo = _hosted_state(tmp_path)
    repo.record_run(RunRecord(id="r1", org_id="default", status="done", ok=True))
    bundle = StoreBundle(
        artifacts=art,
        scenarios=state.scenarios,
        baselines=state.baselines,
        secrets=state.secrets,
        provider_settings=state.providers.store,
    )
    state.org_stores = lambda _org: bundle
    state.run_retention_days = 30

    assert ops.delete_run(state, "r1", actor="editor")[1] == 200
    assert art.list_runs() == []  # tombstoned in the object store
    assert repo.list_runs(org_id="default") == []  # hidden in the DB
    # 40 days on, the sweep purges via both the tombstone and the DB row.
    assert (
        ops.sweep_expired_trash(state, actor="editor", now=datetime.now(UTC) + timedelta(days=40))
        == 1
    )
    assert repo.get_run("r1") is None
    assert fake.list_keys(prefix) == []  # every object gone


def test_sweep_purges_a_db_only_trashed_run(tmp_path: Path) -> None:
    # Hosted edge (BE-0239): a run soft-deleted before any evidence upload has a DB `deleted_at` but
    # no store tombstone, so the store scan misses it — the sweep reconciles against the DB so it is
    # still auto-purged. Here the run has a DB row but no artifact dir.
    state, repo = _hosted_state(tmp_path)
    repo.record_run(RunRecord(id="r1", org_id="default", status="done", ok=True))
    repo.soft_delete_run(
        "r1", org_id="default", deleted_by="editor", at=datetime.now(UTC) - timedelta(days=40)
    )
    state.run_retention_days = 30
    assert state.artifacts.list_trashed_runs() == []  # nothing in the store's trash
    assert ops.sweep_expired_trash(state, actor="editor", now=datetime.now(UTC)) == 1
    assert repo.get_run("r1") is None  # the DB-only-trashed run was purged

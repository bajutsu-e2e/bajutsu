"""The server backend's local start (BE-0449 unit 1, part 3).

`bajutsu serve` with no environment: a default SQLite queue, a generated secrets
key, a ``file://`` store that mounts the runs tree for the default org's runs, ``hosted`` as its own setting, and the run trees
under ``runs/`` indexed into the history at start and on a rescan.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from _shared import project, write_run
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import index_run_trees
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.local_start import hosted_from_env, loopback_base_url

# Every variable that would steer the server backend away from its local defaults.
_SERVER_ENV = (
    "BAJUTSU_SERVER_STORE",
    "BAJUTSU_DATABASE_URL",
    "BAJUTSU_SECRETS_KEY",
    "BAJUTSU_HOSTED",
    "BAJUTSU_OAUTH_GITHUB_CLIENT_ID",
    "BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET",
    "BAJUTSU_OAUTH_GITHUB_REDIRECT_URI",
    "BAJUTSU_OIDC_AUDIENCE",
    "BAJUTSU_LEASE_TIMEOUT_SECONDS",
    "BAJUTSU_LEASE_MAX_ATTEMPTS",
)


@pytest.fixture(autouse=True)
def _no_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _SERVER_ENV:
        monkeypatch.delenv(var, raising=False)


def _local(tmp_path: Path, **kwargs: Any) -> srv.ServeState:
    # A test that wrote run trees first has already laid the project out.
    if not (tmp_path / "runs").exists():
        project(tmp_path)
    return srv._build_state(
        runs_dir=tmp_path / "runs",
        config=tmp_path / "bajutsu.config.yaml",
        scenarios_dir=None,
        root=tmp_path,
        # Explicit, so the store's baselines mount stays inside the test's directory.
        baselines_dir=tmp_path / "baselines",
        max_concurrent=4,
        token=None,
        **kwargs,
    )


# --- the state a local start builds ---


def test_a_server_backend_starts_with_no_environment(tmp_path: Path) -> None:
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.db_executor import DbQueueExecutor
    from bajutsu.serve.server.file_object_store import FileObjectStore
    from bajutsu.serve.server.secrets import DbSecretStore

    state = _local(tmp_path)

    state_dir = tmp_path / ".bajutsu"
    assert isinstance(state.repository, SqlRepository)
    assert (state_dir / "queue.db").is_file()
    # The schema is migrated: the runs table answers a query.
    assert state.repository.list_runs(org_id="default") == []
    assert type(state.executor) is DbQueueExecutor
    assert isinstance(state.secrets, DbSecretStore)
    for key_file in ("secrets.key", "store-signing.key"):
        assert stat.S_IMODE(os.lstat(state_dir / key_file).st_mode) == 0o600
    assert isinstance(state.object_store, FileObjectStore)
    # The default org's runs are the runs tree; every other object sits under the state directory.
    assert state.object_store.mount == ("artifacts/", (tmp_path / "runs").resolve())
    assert state.object_store.root == (state_dir / "objects").resolve()
    assert state.object_store_prefix == ""
    assert state.local_file_store is True
    assert state.hosted is False
    # The default org exists, so a secret or a run row has its foreign key from the first request.
    assert state.repository.get_org("default") is not None


def test_a_secret_round_trips_through_the_generated_key(tmp_path: Path) -> None:
    state = _local(tmp_path)
    state.secrets.set("API_TOKEN", "s3cret")
    # A restart reads the same key file, so the stored secret still decrypts.
    again = _local(tmp_path)
    assert again.secrets.describe("API_TOKEN") is not None


def test_the_signed_urls_point_at_the_bound_port_over_loopback(tmp_path: Path) -> None:
    state = _local(tmp_path, host="0.0.0.0", port=9123)
    assert state.object_store is not None
    url = urlsplit(state.object_store.presigned_url("artifacts/r1/manifest.json"))
    assert (url.scheme, url.netloc) == ("http", "127.0.0.1:9123")
    assert url.path == "/api/store/objects/artifacts/r1/manifest.json"


def test_a_configured_database_without_a_key_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    with pytest.raises(ValueError, match="BAJUTSU_SECRETS_KEY is required"):
        _local(tmp_path)
    # No key was minted for the operator's database.
    assert not (tmp_path / ".bajutsu" / "secrets.key").exists()


def test_a_configured_database_is_used_and_not_migrated_by_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from bajutsu.serve.server.db import engine_from_url
    from bajutsu.serve.server.models import Base

    db_url = f"sqlite:///{tmp_path / 'operator.db'}"
    Base.metadata.create_all(engine_from_url(db_url))
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", db_url)
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    state = _local(tmp_path)
    assert state.repository is not None
    assert not (tmp_path / ".bajutsu" / "queue.db").exists()
    assert not (tmp_path / ".bajutsu" / "secrets.key").exists()


def test_a_server_store_keeps_the_bucket_and_stays_hosted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from bajutsu.serve.server.db_executor import DbQueueExecutor
    from bajutsu.serve.server.file_object_store import FileObjectStore

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt/tenant/")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    # A bucket names its database (BE-0449 unit 9): a SQLite file per replica would split the queue.
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", f"sqlite:///{tmp_path / 'configured.db'}")
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    state = _local(tmp_path)
    assert state.object_store is not None
    assert not isinstance(state.object_store, FileObjectStore)
    assert state.object_store_prefix == "tenant/"
    assert state.local_file_store is False
    assert state.hosted is True
    assert type(state.executor) is DbQueueExecutor
    # None of the local defaults apply: the configured database is the queue.
    assert not (tmp_path / ".bajutsu").exists()


def test_a_local_start_refuses_an_ephemeral_port(tmp_path: Path) -> None:
    # The signed URLs are fixed before any socket is bound, so port 0 would leave them pointing at
    # port 0; the start refuses it instead, before creating any local state.
    with pytest.raises(ValueError, match=r"fixed --port \(not 0\)"):
        _local(tmp_path, port=0)
    assert not (tmp_path / ".bajutsu").exists()


def test_bajutsu_hosted_overrides_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAJUTSU_HOSTED", "true")
    assert _local(tmp_path).hosted is True


def test_a_malformed_bajutsu_hosted_fails_before_any_state_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAJUTSU_HOSTED", "yes")
    with pytest.raises(ValueError, match="BAJUTSU_HOSTED"):
        _local(tmp_path)
    assert not (tmp_path / ".bajutsu").exists()


# --- hosted_from_env ---


@pytest.mark.parametrize(
    ("raw", "store_set", "expected"),
    [
        (None, False, False),
        (None, True, True),
        ("", True, True),
        ("true", False, True),
        ("TRUE", False, True),
        ("1", False, True),
        (" false ", True, False),
        ("0", True, False),
    ],
)
def test_hosted_from_env(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, store_set: bool, expected: bool
) -> None:
    if raw is not None:
        monkeypatch.setenv("BAJUTSU_HOSTED", raw)
    assert hosted_from_env(server_store_set=store_set) is expected


@pytest.mark.parametrize("raw", ["yes", "on", "2", "hosted"])
def test_hosted_from_env_rejects_anything_else(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("BAJUTSU_HOSTED", raw)
    with pytest.raises(ValueError, match=f"got {raw!r}"):
        hosted_from_env(server_store_set=False)


# --- small helpers ---


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "http://127.0.0.1:8765"),
        ("0.0.0.0", "http://127.0.0.1:8765"),
        ("::", "http://127.0.0.1:8765"),
        ("", "http://127.0.0.1:8765"),
        ("::1", "http://[::1]:8765"),
        ("mac.local", "http://mac.local:8765"),
    ],
)
def test_loopback_base_url(host: str, expected: str) -> None:
    assert loopback_base_url(host, 8765) == expected


# --- run history ---


def test_existing_run_trees_are_indexed_at_start(tmp_path: Path) -> None:
    _scn, _cfg, runs = project(tmp_path)
    write_run(runs, "20260101-000000", ok=True, scenarios=[("smoke", True)], target="demo")
    write_run(runs, "20260102-000000", ok=False, scenarios=[("smoke", False)])
    state = _local(tmp_path)
    assert state.repository is not None
    recorded = {r.id: r for r in state.repository.list_runs(org_id="default")}
    assert set(recorded) == {"20260101-000000", "20260102-000000"}
    assert recorded["20260101-000000"].ok is True
    assert recorded["20260101-000000"].target == "demo"
    assert recorded["20260102-000000"].summary["passed"] == 0


def test_indexing_is_idempotent_and_skips_broken_trees(tmp_path: Path) -> None:
    _scn, _cfg, runs = project(tmp_path)
    write_run(runs, "20260101-000000", ok=True, scenarios=[("smoke", True)])
    (runs / "20260103-000000").mkdir()  # no manifest: a run still being written
    broken = runs / "20260104-000000"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    (runs / ".trash").mkdir()  # not a run id
    (runs / "stray.txt").write_text("x", encoding="utf-8")  # not a directory
    state = _local(tmp_path)
    assert state.repository is not None
    assert [r.id for r in state.repository.list_runs(org_id="default")] == ["20260101-000000"]
    # A second pass records nothing new and leaves the existing row alone.
    assert index_run_trees(state) == 0
    assert [r.id for r in state.repository.list_runs(org_id="default")] == ["20260101-000000"]


def test_indexing_keeps_the_runs_in_the_order_they_were_written(tmp_path: Path) -> None:
    _scn, _cfg, runs = project(tmp_path)
    write_run(runs, "b-older", ok=True, scenarios=[("smoke", True)])
    write_run(runs, "a-newer", ok=True, scenarios=[("smoke", True)])
    os.utime(runs / "b-older" / "manifest.json", (1_000_000, 1_000_000))
    os.utime(runs / "a-newer" / "manifest.json", (2_000_000, 2_000_000))
    state = _local(tmp_path)
    assert state.repository is not None
    assert [r.id for r in state.repository.list_runs(org_id="default")] == ["a-newer", "b-older"]


def test_indexing_is_a_no_op_without_a_repository(tmp_path: Path) -> None:
    _scn, _cfg, runs = project(tmp_path)
    write_run(runs, "20260101-000000", ok=True, scenarios=[("smoke", True)])
    assert index_run_trees(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path)) == 0


def test_one_failing_run_does_not_keep_the_rest_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _local(tmp_path)
    runs = tmp_path / "runs"
    write_run(runs, "r-bad", ok=True, scenarios=[("smoke", True)])
    write_run(runs, "r-good", ok=True, scenarios=[("smoke", True)])
    assert state.repository is not None
    real = state.repository.record_run_if_absent

    def flaky(run: Any) -> bool:
        if run.id == "r-bad":
            raise RuntimeError("disk full")
        return real(run)

    monkeypatch.setattr(state.repository, "record_run_if_absent", flaky)
    assert index_run_trees(state) == 1
    assert [r.id for r in state.repository.list_runs(org_id="default")] == ["r-good"]


def test_the_rescan_picks_up_a_run_written_after_start(tmp_path: Path) -> None:
    state = _local(tmp_path)
    write_run(tmp_path / "runs", "20260105-000000", ok=True, scenarios=[("smoke", True)])
    assert ops.rescan_runs(state) == ({"indexed": 1}, 200)
    assert ops.rescan_runs(state) == ({"indexed": 0}, 200)


def test_the_rescan_records_who_rescanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    state = _local(tmp_path)
    assert state.repository is not None
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(state.repository, "record_audit", lambda **kw: audits.append(kw))
    write_run(tmp_path / "runs", "20260105-000000", ok=True, scenarios=[("smoke", True)])
    with caplog.at_level(logging.INFO, logger="bajutsu.serve.operations"):
        result = ops.rescan_runs(state, actor="alice")
    assert result == ({"indexed": 1}, 200)
    assert audits == [
        {
            "org_id": state.org_of("alice"),
            "actor_id": "alice",
            "action": "runs.rescan",
            "target": "",
            "detail": {"indexed": 1},
        }
    ]
    record = next(r for r in caplog.records if getattr(r, "event", None) == "runs.rescanned")
    assert (getattr(record, "actor", None), getattr(record, "indexed", None)) == ("alice", 1)


def test_the_rescan_route_passes_the_actor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def _rescan(state: Any, *, actor: str | None = None) -> tuple[Any, int]:
        seen.append(actor)
        return {"indexed": 0}, 200

    monkeypatch.setattr(ops, "rescan_runs", _rescan)
    client = TestClient(make_app(_local(tmp_path)))
    resp = client.post("/api/runs/rescan")
    assert resp.json() == {"indexed": 0}
    assert len(seen) == 1


def test_the_rescan_route_is_admin_gated() -> None:
    assert ops.required_role("POST", "/api/runs/rescan") == "admin"
    # The neighbouring run-lifecycle routes keep their own tier.
    assert ops.required_role("POST", "/api/runs/bulk-delete") == "editor"
    assert ops.required_role("POST", "/api/runs/r1/restore") == "editor"


def test_a_run_tree_shows_in_the_history_and_its_artifacts_resolve(tmp_path: Path) -> None:
    _scn, _cfg, runs = project(tmp_path)
    write_run(runs, "20260101-000000", ok=True, scenarios=[("smoke", True)])
    client = TestClient(make_app(_local(tmp_path)))

    listed = client.get("/api/runs").json()
    ids = [r["id"] for r in (listed["runs"] if isinstance(listed, dict) else listed)]
    assert ids == ["20260101-000000"]

    # The report is served from the runs tree, through a signed redirect to this server's store.
    resp = client.get("/runs/20260101-000000/report.html", follow_redirects=False)
    assert resp.status_code in (302, 307)
    target = urlsplit(resp.headers["location"])
    assert target.path == "/api/store/objects/artifacts/20260101-000000/report.html"
    body = client.get(f"{target.path}?{target.query}")
    assert body.status_code == 200
    assert body.text == "<html></html>"

    # A run written from the command line while the server is up appears after a rescan.
    write_run(runs, "20260102-000000", ok=False, scenarios=[("smoke", False)])
    assert client.post("/api/runs/rescan").json() == {"indexed": 1}
    listed = client.get("/api/runs").json()
    ids = [r["id"] for r in (listed["runs"] if isinstance(listed, dict) else listed)]
    assert ids[0] == "20260102-000000"


def test_a_worker_uploads_run_artifacts_into_the_runs_tree(tmp_path: Path) -> None:
    state = _local(tmp_path)
    assert state.repository is not None
    state.repository.enqueue_job("1", "default", {"kind": "run"})
    leased = state.repository.lease_job("w1")
    assert leased is not None
    body, status = ops.worker_artifact_urls(
        state,
        {"job_id": "1", "worker_id": "w1", "run_id": "r9", "files": ["manifest.json"]},
    )
    assert status == 200
    assert (
        urlsplit(body["urls"]["manifest.json"]).path
        == "/api/store/objects/artifacts/r9/manifest.json"
    )
    assert state.object_store is not None
    from bajutsu.serve.server.file_object_store import FileObjectStore

    assert isinstance(state.object_store, FileObjectStore)
    assert state.object_store.path_for("artifacts/r9/manifest.json") == (
        (tmp_path / "runs").resolve() / "r9" / "manifest.json"
    )


def test_serve_switches_a_local_start_to_the_fastapi_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[str] = []

    class _Server:
        def run(self) -> None:
            ran.append("asgi")

    monkeypatch.setattr(srv, "make_asgi_server", lambda *_a, **_k: _Server())
    monkeypatch.setattr(srv, "make_server", lambda *_a, **_k: pytest.fail("stdlib transport"))
    # Leave the process-wide logging configuration to the tests that own it.
    monkeypatch.setattr(srv, "_configure_oplog", lambda _state: None)
    _scn, cfg, runs = project(tmp_path)
    srv.serve("127.0.0.1", 8799, None, cfg, runs, root=tmp_path)
    assert ran == ["asgi"]
    assert "using --asgi" in capsys.readouterr().out


# --- review follow-ups: isolation, rescan scope, and indexing races ---


def _seed_foreign_objects(state: srv.ServeState) -> dict[str, Path]:
    """Store another org's run and one object per store folder, as jobs would."""
    from bajutsu.serve.server.file_object_store import FileObjectStore

    store = state.object_store
    assert isinstance(store, FileObjectStore)
    keys = {
        "acme": "acme/artifacts/r1/manifest.json",
        "scenarios": "scenarios/demo/smoke.yaml",
        "baselines": "baselines/home.png",
        "uploads": "uploads/" + "0" * 64 + ".zip",
    }
    for key in keys.values():
        store.put_bytes(key, b'{"ok": true, "scenarios": []}')
    return {name: store.path_for(key) for name, key in keys.items()}


def test_other_orgs_and_store_folders_stay_out_of_the_runs_tree(tmp_path: Path) -> None:
    state = _local(tmp_path)
    paths = _seed_foreign_objects(state)
    runs = (tmp_path / "runs").resolve()
    assert all(not p.is_relative_to(runs) for p in paths.values())
    # The default org's history sees none of them, even after a rescan.
    assert ops.rescan_runs(state) == ({"indexed": 0}, 200)
    assert state.artifacts.list_runs() == []


@pytest.mark.parametrize("name", ["acme", "scenarios", "baselines", "uploads"])
def test_a_default_org_actor_cannot_reach_another_tree(tmp_path: Path, name: str) -> None:
    state = _local(tmp_path)
    paths = _seed_foreign_objects(state)
    client = TestClient(make_app(state))
    rel = {"acme": "r1/manifest.json", "scenarios": "demo/smoke.yaml"}.get(name, "x")
    assert client.get(f"/runs/{name}/{rel}", follow_redirects=False).status_code == 404
    assert client.get(f"/runs/{name}/archive.zip").status_code == 404
    # Neither a soft-delete nor a purge reaches the tree behind the name.
    soft = client.delete(f"/api/runs/{name}")
    purge = client.delete(f"/api/runs/{name}?purge=true")
    bulk = client.post("/api/runs/bulk-delete", json={"ids": [name], "purge": True})
    assert soft.status_code == 404
    assert purge.status_code == 404
    assert bulk.json()["notFound"] == [name]
    assert paths[name].is_file()


def test_the_rescan_needs_the_local_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    _scn, _cfg, runs = project(tmp_path)
    write_run(runs, "20260101-000000", ok=True, scenarios=[("smoke", True)])
    state = _local(tmp_path)
    assert state.repository is not None
    assert index_run_trees(state) == 0
    body, status = ops.rescan_runs(state)
    assert status == 409
    assert "local file store" in body["error"]


def test_only_the_rescan_post_is_admin() -> None:
    assert ops.required_role("POST", "/api/runs/rescan") == "admin"
    # Any other verb on the path is a run's lifecycle, as for any run id.
    assert ops.required_role("DELETE", "/api/runs/rescan") == "editor"


def test_indexing_never_overwrites_a_row_a_job_wrote(tmp_path: Path) -> None:
    from bajutsu.serve.server.db import RunRecord

    state = _local(tmp_path)
    repo = state.repository
    assert repo is not None
    # The job's completion lands first, with its label, then the tree shows up for a rescan.
    repo.record_run(
        RunRecord(id="r1", org_id="default", status="done", ok=True, label="nightly", target="demo")
    )
    write_run(tmp_path / "runs", "r1", ok=False, scenarios=[("smoke", False)])
    assert index_run_trees(state) == 0
    row = repo.get_run("r1")
    assert row is not None
    assert (row.label, row.ok) == ("nightly", True)
    # The insert-only write itself refuses an existing id, so a race past the read loses quietly.
    assert repo.record_run_if_absent(RunRecord(id="r1", org_id="default", status="done")) is False


def test_record_run_if_absent_raises_a_foreign_key_failure(tmp_path: Path) -> None:
    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    from bajutsu.serve.server.db import RunRecord

    repo = _local(tmp_path).repository
    assert repo is not None
    # SQLite enforces foreign keys only when asked; ask on every checkout so the insert below fails.
    engine = repo._engine  # type: ignore[attr-defined]
    event.listen(engine, "checkout", lambda conn, *_: conn.execute("PRAGMA foreign_keys=ON"))
    # No such org: the failure is not a lost race on the id, so it must not read as "already there".
    with pytest.raises(IntegrityError):
        repo.record_run_if_absent(RunRecord(id="r1", org_id="no-such-org", status="done"))
    assert repo.get_run("r1") is None


def test_indexing_skips_a_run_an_in_process_job_is_still_writing(tmp_path: Path) -> None:
    state = _local(tmp_path)
    write_run(tmp_path / "runs", "r-live", ok=True, scenarios=[("smoke", True)])
    job = state.job_registry.register(srv.Job(cmd=["bajutsu", "run"]))
    job.run_id = "r-live"
    assert index_run_trees(state) == 0
    job.status = "done"
    assert index_run_trees(state) == 1


def test_a_manifest_symlinked_out_of_the_runs_tree_is_skipped(tmp_path: Path) -> None:
    state = _local(tmp_path)
    runs = tmp_path / "runs"
    outside = tmp_path / "elsewhere.json"
    outside.write_text('{"ok": true, "scenarios": []}', encoding="utf-8")
    (runs / "r-link").mkdir()
    (runs / "r-link" / "manifest.json").symlink_to(outside)
    write_run(runs, "r-good", ok=True, scenarios=[("smoke", True)])
    # A symlink that stays inside the tree is refused too: it would file one run under another id.
    (runs / "r-intree").mkdir()
    (runs / "r-intree" / "manifest.json").symlink_to(Path("..") / "r-good" / "manifest.json")
    assert index_run_trees(state) == 1
    assert state.repository is not None
    assert state.repository.get_run("r-link") is None
    assert state.repository.get_run("r-intree") is None
    assert state.repository.get_run("r-good") is not None


def test_a_trashed_run_is_not_indexed_back(tmp_path: Path) -> None:
    state = _local(tmp_path)
    runs = tmp_path / "runs"
    write_run(runs, "r-trashed", ok=True, scenarios=[("smoke", True)])
    # The soft-delete tombstone outlives a lost database row; the run must stay trashed.
    (runs / "r-trashed" / ".deleted").write_text("2026-01-01T00:00:00+00:00", encoding="utf-8")
    assert index_run_trees(state) == 0
    assert state.repository is not None
    assert state.repository.get_run("r-trashed") is None


def test_a_completing_job_wins_a_race_with_a_rescan(tmp_path: Path) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.db import RunRecord

    state = _local(tmp_path)
    repo = state.repository
    assert repo is not None

    # The rescan's insert lands after the job's merge has looked the id up and found nothing, so
    # the merge's own insert collides on the primary key.
    def rescan_lands_first(_session: Session, _ctx: Any, _instances: Any) -> None:
        assert repo.record_run_if_absent(RunRecord(id="r1", org_id="default", status="done"))

    event.listen(Session, "before_flush", rescan_lands_first, once=True)
    try:
        repo.record_run(
            RunRecord(id="r1", org_id="default", status="done", ok=True, label="nightly")
        )
    finally:
        if event.contains(Session, "before_flush", rescan_lands_first):
            event.remove(Session, "before_flush", rescan_lands_first)
    row = repo.get_run("r1")
    assert row is not None
    assert (row.label, row.ok) == ("nightly", True)


def test_a_run_soft_deleted_under_the_server_backend_stays_hidden_locally(tmp_path: Path) -> None:
    # The server backend trashes a run in place with a `.deleted` tombstone; the local backend's
    # history and the CLI history readers walk the same tree, so they must skip it too.
    from bajutsu.cli._shared import read_manifests
    from bajutsu.serve.helpers import list_crawl_runs, list_runs

    state = _local(tmp_path)
    runs = tmp_path / "runs"
    write_run(runs, "20260101-000000", ok=True, scenarios=[("smoke", True)])
    write_run(runs, "20260102-000000", ok=True, scenarios=[("smoke", True)])
    (runs / "20260102-000000" / "screenmap.json").write_text("{}")
    (runs / "20260101-000000" / "screenmap.json").write_text("{}")
    result = ops.delete_run(state, "20260102-000000")
    assert result[1] == 200
    assert (runs / "20260102-000000" / ".deleted").is_file()  # trashed in place, not moved

    assert [r["id"] for r in list_runs(runs)] == ["20260101-000000"]
    assert [r["id"] for r in list_crawl_runs(runs)] == ["20260101-000000"]
    assert len(read_manifests(runs)) == 1


def test_serve_registers_no_batch_provider_even_with_a_project_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The AWS role lives in a Device Farm worker alone (BE-0448): a server started with the project
    # ARN in its environment still registers nothing, so it can submit nothing.
    from bajutsu.serve import batch_provider as bp

    class _Server:
        def run(self) -> None:
            pass

    monkeypatch.setenv("DEVICEFARM_PROJECT_ARN", "arn:aws:devicefarm:us-west-2:1:project:p")
    monkeypatch.setattr(srv, "make_asgi_server", lambda *_a, **_k: _Server())
    monkeypatch.setattr(srv, "_configure_oplog", lambda _state: None)
    saved = dict(bp._PROVIDERS)
    bp._PROVIDERS.clear()
    try:
        _scn, cfg, runs = project(tmp_path)
        srv.serve("127.0.0.1", 8798, None, cfg, runs, root=tmp_path)
        assert bp._PROVIDERS == {}
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)

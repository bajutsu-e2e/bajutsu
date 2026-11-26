"""BE-0510: storing an imported run, recording it, and undoing a storage that failed."""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _run_import import RUN_ID, manifest, minimal_files, real_run, run_zip, zip_files
from _shared import FakeObjectStore
from sqlalchemy import Engine

from bajutsu.serve import jobs
from bajutsu.serve import operations as ops
from bajutsu.serve.operations import run_import
from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base
from bajutsu.serve.state import ServeState
from bajutsu.serve.uploads import BundleError, run_bundle

_REPLY = {
    "runId": RUN_ID,
    "ok": True,
    "url": f"/runs/{RUN_ID}/report.html",
    "archive": f"/runs/{RUN_ID}/archive.zip",
    "dropped": [],
}


def _repo(serve_engine: Callable[..., Engine]) -> SqlRepository:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    for org in ("default", "acme"):
        repo.ensure_org(org, slug=org, name=org)
    repo.upsert_user("editor", org_id="default", github_login="editor", email="e@x", role="editor")
    repo.upsert_user("ann", org_id="acme", github_login="ann", email="n@x", role="editor")
    return repo


def _hosted(
    serve_engine: Callable[..., Engine], tmp_path: Path, store: FakeObjectStore | None = None
) -> tuple[ServeState, SqlRepository, FakeObjectStore]:
    repo = _repo(serve_engine)
    store = store or FakeObjectStore()
    state = ServeState(runs_dir=tmp_path / "runs", repository=repo)
    state.object_store = store
    state.artifacts = ObjectStorageArtifactStore(store, prefix="artifacts/")
    return state, repo, store


def _zip(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    files = minimal_files()
    files.update({f"{RUN_ID}/{name}": data for name, data in (extra or {}).items()})
    return zip_files(tmp_path / "run.zip", files)


def _staging_leftovers(state: ServeState) -> list[Path]:
    root = state.runs_dir.parent / f".{state.runs_dir.name}-import"
    return list(root.iterdir()) if root.is_dir() else []


def test_a_real_run_imports_into_the_runs_tree_without_a_repository(tmp_path: Path) -> None:
    run_dir = real_run(tmp_path / "laptop")
    state = ServeState(runs_dir=tmp_path / "runs")

    body, status = ops.import_run(state, run_zip(run_dir, tmp_path / "run.zip"), actor=None)

    assert status == 200
    assert body["url"] == f"/runs/{RUN_ID}/report.html"
    assert any(p.endswith("hierarchy.raw.xml") for p in body["dropped"])
    assert ops.run_file(state.artifacts, f"{RUN_ID}/report.html") is not None
    assert [r["id"] for r in state.artifacts.list_runs()] == [RUN_ID]
    assert _staging_leftovers(state) == []


def test_a_hosted_import_stores_the_objects_and_records_the_run(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)

    body, status = ops.import_run(state, _zip(tmp_path), actor="editor")

    assert (body, status) == (_REPLY, 200)
    assert sorted(store.objects) == sorted(
        f"artifacts/{RUN_ID}/{name}"
        for name in ("ctrf.json", "junit.xml", "manifest.json", "report.html", "scenario.yaml")
    )
    # The manifest goes last, so a half-copied run never reads as finished.
    assert store.put_calls[-1] == f"artifacts/{RUN_ID}/manifest.json"
    assert ops.run_file(state.artifacts, f"{RUN_ID}/report.html") is not None
    (row,) = repo.list_runs(org_id="default")
    assert (row.status, row.ok, row.created_by) == ("done", True, "editor")
    assert row.summary["report"] is True
    stored = json.loads(store.objects[f"artifacts/{RUN_ID}/manifest.json"])
    assert stored["provenance"]["source"] == "import"
    assert _staging_leftovers(state) == []


def test_an_import_lands_in_the_callers_organization(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)

    _, status = ops.import_run(state, _zip(tmp_path), actor="ann")

    assert status == 200
    assert [r.id for r in repo.list_runs(org_id="acme")] == [RUN_ID]
    assert repo.list_runs(org_id="default") == []
    assert all(key.startswith(f"acme/artifacts/{RUN_ID}/") for key in store.objects)


def test_a_rejected_zip_stores_nothing(serve_engine: Callable[..., Engine], tmp_path: Path) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)

    body, status = ops.import_run(state, _zip(tmp_path, {"../x.txt": "x"}), actor="editor")

    assert status == 400
    assert "unsafe entry path" in body["error"]
    assert store.objects == {}
    assert repo.get_run(RUN_ID) is None
    assert _staging_leftovers(state) == []


def test_another_organizations_id_collides_with_the_same_answer(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo, _ = _hosted(serve_engine, tmp_path)
    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 200

    same_org = ops.import_run(state, _zip(tmp_path), actor="editor")
    other_org = ops.import_run(state, _zip(tmp_path), actor="ann")

    assert same_org == other_org == ({"error": "a run with this id already exists"}, 409)
    assert repo.list_runs(org_id="acme") == []


@pytest.mark.parametrize("held", [f"{RUN_ID}/manifest.json", f".trash/{RUN_ID}/.deleted"])
def test_a_tree_that_already_holds_the_id_collides(tmp_path: Path, held: str) -> None:
    state = ServeState(runs_dir=tmp_path / "runs")
    (state.runs_dir / held).parent.mkdir(parents=True)
    (state.runs_dir / held).write_text("{}")

    assert ops.import_run(state, _zip(tmp_path), actor=None)[1] == 409


def test_a_tombstoned_prefix_collides(serve_engine: Callable[..., Engine], tmp_path: Path) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)
    store.objects[f"artifacts/{RUN_ID}/.deleted"] = b"2026-01-01T00:00:00+00:00"

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 409
    assert repo.get_run(RUN_ID) is None


class _RacingStore(FakeObjectStore):
    """Holds the first upload until a second import of the same id has been answered."""

    def __init__(self) -> None:
        super().__init__()
        self.during: Callable[[], None] | None = None

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        during, self.during = self.during, None
        if during is not None:
            during()
        super().put_file(key, path, content_type=content_type)


def test_two_concurrent_imports_of_one_id_admit_one(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    store = _RacingStore()
    state, repo, _ = _hosted(serve_engine, tmp_path, store)
    answers: list[int] = []
    # The second import runs while the first has reserved the id but placed no file yet, so only
    # the reservation can refuse it.
    store.during = lambda: answers.append(ops.import_run(state, _zip(tmp_path), actor="ann")[1])

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 200

    assert answers == [409]
    assert [r.org_id for r in repo.list_runs(org_id="default")] == ["default"]


def test_a_reservation_is_hidden_from_the_history_while_files_are_placed(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    store = _RacingStore()
    state, repo, _ = _hosted(serve_engine, tmp_path, store)
    seen: list[Any] = []
    store.during = lambda: seen.append(
        (repo.list_runs(org_id="default"), getattr(repo.get_run(RUN_ID), "status", None))
    )

    ops.import_run(state, _zip(tmp_path), actor="editor")

    assert seen == [([], "importing")]


class _FailingStore(FakeObjectStore):
    """Fails the upload of any key ending in *suffix*."""

    def __init__(self, suffix: str) -> None:
        super().__init__()
        self.suffix = suffix

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        if key.endswith(self.suffix):
            raise OSError("boom")
        super().put_file(key, path, content_type=content_type)


@pytest.mark.parametrize("suffix", ["report.html", "manifest.json"])
def test_a_failed_placement_removes_what_it_uploaded_and_frees_the_id(
    serve_engine: Callable[..., Engine], tmp_path: Path, suffix: str
) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path, _FailingStore(suffix))

    body, status = ops.import_run(state, _zip(tmp_path), actor="editor")

    assert (body, status) == ({"error": "could not store the run"}, 500)
    assert store.objects == {}
    assert repo.get_run(RUN_ID) is None


def test_a_failed_rename_frees_the_id(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(serve_engine)
    state = ServeState(runs_dir=tmp_path / "runs", repository=repo)

    def fail(self: Path, target: Path) -> Path:
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "rename", fail)

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 500
    assert repo.get_run(RUN_ID) is None


def _reserve(repo: SqlRepository, org: str, *, age: timedelta, run_id: str = RUN_ID) -> None:
    repo.record_run(
        RunRecord(id=run_id, org_id=org, status="importing", created_at=datetime.now(UTC) - age)
    )


def test_the_callers_stale_reservation_is_purged_with_its_orphaned_objects(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)
    _reserve(repo, "default", age=timedelta(hours=2))
    store.objects[f"artifacts/{RUN_ID}/orphan.png"] = b"x"

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 200

    assert f"artifacts/{RUN_ID}/orphan.png" not in store.objects
    assert [r.status for r in repo.list_runs(org_id="default")] == ["done"]


@pytest.mark.parametrize(("org", "age"), [("acme", 2), ("default", 0)])
def test_another_organizations_or_a_fresh_reservation_is_left_alone(
    serve_engine: Callable[..., Engine], tmp_path: Path, org: str, age: int
) -> None:
    state, repo, _ = _hosted(serve_engine, tmp_path)
    _reserve(repo, org, age=timedelta(hours=age))

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 409

    row = repo.get_run(RUN_ID)
    assert row is not None
    assert (row.org_id, row.status) == (org, "importing")


def test_the_start_up_sweep_clears_every_organizations_stale_imports(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)
    _reserve(repo, "default", age=timedelta(hours=2), run_id="stale-default")
    _reserve(repo, "acme", age=timedelta(hours=2), run_id="stale-acme")
    _reserve(repo, "default", age=timedelta(0), run_id="fresh")
    store.objects.update(
        {
            "artifacts/stale-default/a.png": b"x",
            "acme/artifacts/stale-acme/a.png": b"x",
            "artifacts/fresh/a.png": b"x",
        }
    )
    staging = state.runs_dir.parent / f".{state.runs_dir.name}-import"
    (staging / "old").mkdir(parents=True)
    (staging / "new").mkdir()
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(staging / "old", (two_hours_ago, two_hours_ago))

    ops.sweep_stale_imports(state)

    assert sorted(store.objects) == ["artifacts/fresh/a.png"]
    assert [repo.get_run(r) is None for r in ("stale-default", "stale-acme", "fresh")] == [
        True,
        True,
        False,
    ]
    assert [p.name for p in staging.iterdir()] == ["new"]


def test_a_finished_run_older_than_an_hour_survives_a_reimport(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)
    old = datetime.now(UTC) - timedelta(hours=2)
    repo.record_run(RunRecord(id=RUN_ID, org_id="default", status="done", created_at=old))
    store.objects[f"artifacts/{RUN_ID}/keep.png"] = b"x"

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 409

    assert list(store.objects) == [f"artifacts/{RUN_ID}/keep.png"]
    assert [r.status for r in repo.list_runs(org_id="default")] == ["done"]


def test_a_tree_that_appears_during_the_import_is_not_overwritten(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(serve_engine)
    state = ServeState(runs_dir=tmp_path / "runs", repository=repo)
    record = jobs.record_run_row

    def record_then_race(*args: Any, **kwargs: Any) -> bool:
        inserted = record(*args, **kwargs)
        (state.runs_dir / RUN_ID).mkdir(parents=True)
        (state.runs_dir / RUN_ID / "theirs.txt").write_text("theirs")
        return inserted

    monkeypatch.setattr(run_import, "record_run_row", record_then_race)

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 409

    assert (state.runs_dir / RUN_ID / "theirs.txt").read_text() == "theirs"
    assert repo.get_run(RUN_ID) is None


@pytest.mark.parametrize("new_owner", [None, "acme"])
def test_a_lost_reservation_removes_files_only_when_no_one_holds_the_id(
    serve_engine: Callable[..., Engine], tmp_path: Path, new_owner: str | None
) -> None:
    store = _RacingStore()
    state, repo, _ = _hosted(serve_engine, tmp_path, store)

    def take_away() -> None:
        repo.purge_run(RUN_ID, org_id="default")
        if new_owner is not None:
            _reserve(repo, new_owner, age=timedelta(0))

    store.during = take_away

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 500

    assert bool(store.objects) is (new_owner is not None)
    row = repo.get_run(RUN_ID)
    assert (row.org_id if row else None) == new_owner


class _StuckStore(_FailingStore):
    """Fails the upload and then the cleanup too."""

    def delete_keys(self, keys: Iterable[str]) -> None:
        raise OSError("cannot delete")


def test_a_cleanup_that_fails_keeps_the_reservation_for_the_stale_purge(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo, _ = _hosted(serve_engine, tmp_path, _StuckStore("report.html"))

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 500

    row = repo.get_run(RUN_ID)
    assert row is not None
    assert row.status == "importing"


def test_one_stale_import_that_cannot_be_removed_stops_nothing_else(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    class _OneStuck(FakeObjectStore):
        def delete_keys(self, keys: Iterable[str]) -> None:
            keys = list(keys)
            if any("stuck" in key for key in keys):
                raise OSError("cannot delete")
            super().delete_keys(keys)

    state, repo, store = _hosted(serve_engine, tmp_path, _OneStuck())
    _reserve(repo, "default", age=timedelta(hours=2), run_id="stuck")
    _reserve(repo, "default", age=timedelta(hours=2), run_id="gone")
    store.objects.update({"artifacts/stuck/a.png": b"x", "artifacts/gone/a.png": b"x"})

    ops.sweep_stale_imports(state)

    # The row outlives a removal that failed, so a later sweep finds the files again.
    assert repo.get_run("stuck") is not None
    assert repo.get_run("gone") is None


def test_a_runs_directory_on_another_filesystem_is_copied_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = ServeState(runs_dir=tmp_path / "runs")

    def cross_device(self: Path, target: Path) -> Path:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(Path, "rename", cross_device)

    assert ops.import_run(state, _zip(tmp_path), actor=None)[1] == 200

    assert (state.runs_dir / RUN_ID / "manifest.json").is_file()
    assert (state.runs_dir / RUN_ID / "report.html").is_file()


def test_a_full_disk_is_the_servers_error_not_the_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = ServeState(runs_dir=tmp_path / "runs")

    def full(zip_path: Path, dest: Path) -> None:
        raise BundleError("could not extract") from OSError(errno.ENOSPC, "no space left")

    monkeypatch.setattr(run_bundle, "extract_bundle", full)

    assert ops.import_run(state, _zip(tmp_path), actor=None) == (
        {"error": "could not store the run"},
        500,
    )


def test_a_failed_flip_answers_500_and_keeps_the_files_for_the_stale_purge(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, repo, store = _hosted(serve_engine, tmp_path)

    def down(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("database is down")

    monkeypatch.setattr(repo, "finish_import", down)

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 500

    assert store.objects
    row = repo.get_run(RUN_ID)
    assert row is not None
    assert row.status == "importing"


def test_the_history_takes_the_label_and_the_audit_names_the_importer(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, repo, _ = _hosted(serve_engine, tmp_path)
    audits: list[tuple[Any, ...]] = []
    monkeypatch.setattr(repo, "record_audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    files = minimal_files()
    files[f"{RUN_ID}/manifest.json"] = manifest(label="laptop")

    assert ops.import_run(state, zip_files(tmp_path / "run.zip", files), actor="editor")[1] == 200

    assert repo.list_runs(org_id="default")[0].label == "laptop"
    ((args, kwargs),) = audits
    recorded = [str(v) for v in (*args, *kwargs.values())]
    assert {"default", "editor", "run.import", RUN_ID} <= set(recorded)


def test_a_local_start_renames_for_the_default_organization_alone(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _, store = _hosted(serve_engine, tmp_path)
    state.local_file_store = True

    assert ops.import_run(state, _zip(tmp_path), actor="editor")[1] == 200
    assert (state.runs_dir / RUN_ID / "manifest.json").is_file()
    assert store.objects == {}

    other = zip_files(tmp_path / "other.zip", minimal_files(run_id="20261126-111111"))
    assert ops.import_run(state, other, actor="ann")[1] == 200
    assert not (state.runs_dir / "20261126-111111").exists()
    assert all(key.startswith("acme/artifacts/20261126-111111/") for key in store.objects)

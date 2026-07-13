"""The `LocalArtifactStore` soft-delete / restore / purge seam (BE-0239).

Soft-delete parks a run under ``runs/.trash/`` so it drops out of `list_runs`/`list_crawl_runs` but
stays restorable; purge removes it for good. The trash stays inside ``runs_dir`` (so the existing
path-containment holds) and is unreachable through the read path (`get`), so a soft-deleted run is
really gone from view, not merely delisted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bajutsu.serve.artifacts import LocalArtifactStore


def _run(runs_dir: Path, run_id: str, *, ok: bool = True) -> None:
    d = runs_dir / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps({"ok": ok, "scenarios": [{"scenario": "s", "ok": ok}]})
    )
    (d / "report.html").write_text("<html></html>")


def _crawl(runs_dir: Path, run_id: str) -> None:
    d = runs_dir / run_id
    d.mkdir(parents=True)
    (d / "screenmap.json").write_text(json.dumps({"nodes": [{}], "edges": [], "crashes": []}))


def test_soft_delete_drops_a_run_from_the_listing_but_keeps_the_bytes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    _run(tmp_path, "r2")

    assert store.soft_delete_run("r1") is True
    assert [r["id"] for r in store.list_runs()] == ["r2"]  # r1 delisted
    assert (tmp_path / ".trash" / "r1" / "manifest.json").is_file()  # bytes preserved in trash


def test_soft_delete_also_hides_a_crawl_run(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _crawl(tmp_path, "c1")
    assert store.soft_delete_run("c1") is True
    assert store.list_crawl_runs() == []


def test_soft_deleted_run_is_unreachable_through_the_read_path(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    store.soft_delete_run("r1")
    assert store.get("r1/report.html") is None  # the live path is gone
    # The trash must not be a back door to the artifacts either.
    assert store.get(".trash/r1/report.html") is None
    assert store.open_bytes(".trash/r1/report.html") is None


def test_soft_delete_of_a_missing_or_bad_id_is_false(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    assert store.soft_delete_run("nope") is False
    assert store.soft_delete_run("../escape") is False  # not a single safe segment
    assert store.soft_delete_run(".trash") is False  # the trash dir itself never qualifies


def test_restore_returns_a_run_to_the_listing(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    store.soft_delete_run("r1")
    assert store.restore_run("r1") is True
    assert [r["id"] for r in store.list_runs()] == ["r1"]
    assert store.restore_run("r1") is False  # nothing left in the trash to restore


def test_restore_will_not_clobber_a_live_run(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    store.soft_delete_run("r1")
    _run(tmp_path, "r1")  # a fresh live run took the id back
    assert store.restore_run("r1") is False
    assert (tmp_path / "r1").is_dir()  # the live run is intact


def test_re_deleting_a_restored_id_replaces_the_stale_trash_copy(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1", ok=True)
    store.soft_delete_run("r1")
    store.restore_run("r1")
    _run_marker = tmp_path / "r1" / "marker.txt"
    _run_marker.write_text("v2")
    assert store.soft_delete_run("r1") is True  # newest delete wins, no nesting
    assert (tmp_path / ".trash" / "r1" / "marker.txt").read_text() == "v2"


def test_purge_removes_a_trashed_run_for_good(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    store.soft_delete_run("r1")
    assert store.purge_run("r1") is True
    assert not (tmp_path / ".trash" / "r1").exists()
    assert store.restore_run("r1") is False


def test_purge_removes_a_live_run_directly(tmp_path: Path) -> None:
    # The ``?purge=true`` immediate path: purge a run that was never soft-deleted.
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    assert store.purge_run("r1") is True
    assert not (tmp_path / "r1").exists()
    assert store.purge_run("r1") is False  # nothing left anywhere


def test_list_trashed_runs_reports_ids_and_deletion_time(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    _run(tmp_path, "r2")
    store.soft_delete_run("r1")
    store.soft_delete_run("r2")
    trashed = store.list_trashed_runs()
    assert {r["id"] for r in trashed} == {"r1", "r2"}
    assert all(r["deletedAt"] for r in trashed)  # each carries an ISO timestamp
    assert store.list_runs() == []  # both delisted


def test_list_trashed_runs_is_empty_without_a_trash_dir(tmp_path: Path) -> None:
    assert LocalArtifactStore(tmp_path).list_trashed_runs() == []


def test_deleted_at_reflects_delete_time_not_the_runs_file_mtime(tmp_path: Path) -> None:
    # Regression (BE-0239): `deletedAt` must be *when the run was soft-deleted*, not when its files
    # were last written. A rename into .trash/ doesn't touch the dir's mtime, so a run whose bytes
    # are old but was trashed just now must still report a recent deletedAt (else the retention sweep
    # would purge it immediately). Backdate the run's files far into the past, then soft-delete now.
    import os

    store = LocalArtifactStore(tmp_path)
    _run(tmp_path, "r1")
    long_ago = (datetime.now(UTC) - timedelta(days=365)).timestamp()
    for p in (tmp_path / "r1").rglob("*"):
        os.utime(p, (long_ago, long_ago))
    os.utime(tmp_path / "r1", (long_ago, long_ago))

    store.soft_delete_run("r1")
    deleted_at = datetime.fromisoformat(store.list_trashed_runs()[0]["deletedAt"])
    assert deleted_at > datetime.now(UTC) - timedelta(minutes=5)  # ~now, not a year ago

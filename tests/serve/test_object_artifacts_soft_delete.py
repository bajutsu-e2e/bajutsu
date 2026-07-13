"""The object-storage `ArtifactStore` soft-delete / restore / purge seam (BE-0239).

Object storage has no filesystem trash to move a tree into, so a soft-delete writes a
``<run_id>/.deleted`` tombstone (body = the deletion time); `list_runs`/`list_crawl_runs` skip a
tombstoned run, and purge removes every object under the run's prefix. The injected store is an
in-memory fake, so no boto3 / real bucket touches the gate.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore


class FakeObjectStore:
    """A mutable in-memory slice of the ObjectStore seam (exists/get/put/list/delete)."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._objects = dict(objects or {})

    def exists(self, key: str) -> bool:
        return key in self._objects

    def get_bytes(self, key: str) -> bytes | None:
        return self._objects.get(key)

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self._objects[key] = data

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self._objects if k.startswith(prefix)]

    def delete_key(self, key: str) -> None:
        self._objects.pop(key, None)

    def delete_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            self._objects.pop(key, None)


def _manifest(run_id: str) -> dict[str, bytes]:
    return {
        f"runs/{run_id}/manifest.json": json.dumps(
            {"ok": True, "scenarios": [{"scenario": "s", "ok": True}]}
        ).encode(),
        f"runs/{run_id}/report.html": b"<html></html>",
    }


def _screenmap(run_id: str) -> dict[str, bytes]:
    return {
        f"runs/{run_id}/screenmap.json": json.dumps(
            {"nodes": [{}], "edges": [], "crashes": []}
        ).encode()
    }


def _store(objects: dict[str, bytes]) -> ObjectStorageArtifactStore:
    return ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/")


def test_soft_delete_writes_a_tombstone_and_delists_the_run() -> None:
    store = _store({**_manifest("r1"), **_manifest("r2")})
    assert store.soft_delete_run("r1") is True
    assert [r["id"] for r in store.list_runs()] == ["r2"]
    assert store.get("r1/report.html") is not None  # bytes remain until purge


def test_soft_delete_also_delists_a_crawl_run() -> None:
    store = _store(_screenmap("c1"))
    assert store.soft_delete_run("c1") is True
    assert store.list_crawl_runs() == []


def test_soft_delete_of_a_run_with_no_objects_is_false() -> None:
    store = _store(_manifest("r1"))
    assert store.soft_delete_run("ghost") is False  # no keys under its prefix
    assert store.soft_delete_run("../escape") is False  # not a safe segment


def test_soft_delete_is_idempotent_second_call_is_false() -> None:
    store = _store(_manifest("r1"))
    assert store.soft_delete_run("r1") is True
    assert store.soft_delete_run("r1") is False  # already tombstoned


def test_restore_clears_the_tombstone() -> None:
    store = _store(_manifest("r1"))
    store.soft_delete_run("r1")
    assert store.restore_run("r1") is True
    assert [r["id"] for r in store.list_runs()] == ["r1"]
    assert store.restore_run("r1") is False  # nothing tombstoned now


def test_purge_removes_every_object_under_the_run() -> None:
    store = _store({**_manifest("r1"), **_manifest("r2")})
    store.soft_delete_run("r1")
    assert store.purge_run("r1") is True
    assert store.get("r1/report.html") is None  # bytes gone
    assert store.purge_run("r1") is False  # nothing left
    assert [r["id"] for r in store.list_runs()] == ["r2"]  # sibling intact


def test_purge_removes_a_live_run_directly() -> None:
    store = _store(_manifest("r1"))
    assert store.purge_run("r1") is True  # never soft-deleted, purged outright
    assert store.list_runs() == []


def test_list_trashed_runs_reports_tombstoned_ids_and_time() -> None:
    store = _store({**_manifest("r1"), **_manifest("r2")})
    store.soft_delete_run("r1")
    trashed = store.list_trashed_runs()
    assert [r["id"] for r in trashed] == ["r1"]
    assert trashed[0]["deletedAt"]  # an ISO timestamp read from the tombstone body

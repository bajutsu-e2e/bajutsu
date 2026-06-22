"""Tests for the object-storage ArtifactStore (BE-0015 server phase).

`ObjectStorageArtifactStore` is the server implementation of the `ArtifactStore` seam: run
artifacts live in S3-compatible object storage (R2/MinIO) rather than the local filesystem.
`get` hands back a signed-URL redirect (the `Artifact.redirect` the handler already 302s to),
`open_bytes` fetches object bytes, and `list_runs` summarizes the runs from their stored
manifests. The object-store client is injected, so an in-memory fake drives this — no boto3 or
real bucket on the gate.
"""

from __future__ import annotations

import json

from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore


class FakeObjectStore:
    """The slice of an S3-compatible client the store uses, in memory."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def exists(self, key: str) -> bool:
        return key in self._objects

    def get_bytes(self, key: str) -> bytes | None:
        return self._objects.get(key)

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self._objects if k.startswith(prefix)]


def _manifest(ok: bool, scenarios: list[tuple[str, bool]]) -> bytes:
    return json.dumps(
        {"ok": ok, "scenarios": [{"scenario": n, "ok": o} for n, o in scenarios]}
    ).encode()


def test_get_redirects_to_a_signed_url_for_an_existing_object() -> None:
    store = ObjectStorageArtifactStore(
        FakeObjectStore({"runs/r1/report.html": b"<html></html>"}), prefix="runs/"
    )
    art = store.get("r1/report.html")
    assert art is not None
    assert art.body is None  # object storage redirects, never inlines bytes
    assert art.redirect == "https://signed.example/runs/r1/report.html"
    assert "text/html" in art.content_type


def test_get_missing_object_is_none() -> None:
    store = ObjectStorageArtifactStore(FakeObjectStore({}), prefix="runs/")
    assert store.get("r1/missing.png") is None


def test_escaping_rels_are_treated_as_missing() -> None:
    # Same containment as LocalArtifactStore: empty, absolute, and `..` rels never reach storage.
    store = ObjectStorageArtifactStore(
        FakeObjectStore({"secret.txt": b"top secret", "runs/r1/ok.txt": b"ok"}), prefix="runs/"
    )
    for rel in ("", "/etc/passwd", "../secret.txt", "../../secret.txt"):
        assert store.get(rel) is None, rel
        assert store.open_bytes(rel) is None, rel


def test_list_runs_skips_non_object_manifest() -> None:
    # A manifest.json that decodes to a non-dict (e.g. a list) is skipped, not a 500.
    objects = {
        "runs/bad/manifest.json": b"[1, 2, 3]",
        "runs/good/manifest.json": _manifest(True, [("home", True)]),
    }
    listed = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/").list_runs()
    assert [r["id"] for r in listed] == ["good"]


def test_open_bytes_fetches_object_or_none() -> None:
    store = ObjectStorageArtifactStore(
        FakeObjectStore({"runs/r1/sid/visual-actual.png": b"SHOT"}), prefix="runs/"
    )
    assert store.open_bytes("r1/sid/visual-actual.png") == b"SHOT"
    assert store.open_bytes("r1/missing.png") is None


def test_list_runs_summarizes_from_manifests_newest_first() -> None:
    objects = {
        "runs/20260610-1/manifest.json": _manifest(True, [("home", True)]),
        "runs/20260610-1/report.html": b"<html></html>",
        "runs/20260612-9/manifest.json": _manifest(False, [("a", True), ("b", False)]),
    }
    listed = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/").list_runs()
    assert [r["id"] for r in listed] == ["20260612-9", "20260610-1"]  # reverse-lexicographic
    newest, older = listed
    assert newest["ok"] is False and newest["passed"] == 1 and newest["total"] == 2
    assert newest["report"] is False  # no report.html stored for this run
    assert older["report"] is True and older["scenarios"] == ["home"]


def test_archive_zips_objects_under_the_run_id_root() -> None:
    import io
    import zipfile

    store = ObjectStorageArtifactStore(
        FakeObjectStore(
            {
                "runs/r1/report.html": b"<html></html>",
                "runs/r1/demo/shot.png": b"PNG",
                "runs/r2/report.html": b"other",  # a different run must not leak in
            }
        ),
        prefix="runs/",
    )
    art = store.archive("r1")
    assert art is not None and art.content_type == "application/zip" and art.body is not None
    with zipfile.ZipFile(io.BytesIO(art.body)) as zf:
        names = zf.namelist()
    assert names == ["r1/demo/shot.png", "r1/report.html"]  # sorted, rooted at id, no r2 leak


def test_archive_missing_or_escaping_run_id_is_none() -> None:
    store = ObjectStorageArtifactStore(FakeObjectStore({}), prefix="runs/")
    assert store.archive("nope") is None  # no objects
    assert store.archive("../etc") is None  # escapes the prefix

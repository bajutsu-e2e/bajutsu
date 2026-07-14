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


def _screenmap(nodes: int, edges: int, crashes: int, *, stop_reason: str = "") -> bytes:
    data: dict[str, object] = {
        "nodes": [{} for _ in range(nodes)],
        "edges": [{} for _ in range(edges)],
        "crashes": [{} for _ in range(crashes)],
    }
    if stop_reason:
        data["stop_reason"] = stop_reason
    return json.dumps(data).encode()


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
        assert store.exists(rel) is False, rel


def test_exists_matches_object_presence_without_a_presigned_url() -> None:
    store = ObjectStorageArtifactStore(
        FakeObjectStore({"runs/r1/elements.json": b"[]"}), prefix="runs/"
    )
    assert store.exists("r1/elements.json") is True
    assert store.exists("r1/missing.json") is False


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
    store = ObjectStorageArtifactStore(
        FakeObjectStore({"runs/r1/demo/shot.png": b"PNG"}), prefix="runs/"
    )
    assert store.archive("nope") is None  # no objects
    assert store.archive("../etc") is None  # escapes the prefix
    assert store.archive("r1/demo") is None  # a nested segment isn't a run id


def test_list_crawl_runs_summarizes_from_screenmaps_newest_first() -> None:
    objects = {
        "runs/20260610-1/screenmap.json": _screenmap(2, 1, 0),
        "runs/20260610-1/flows/login.yaml": b"name: login",
        "runs/20260612-9/screenmap.json": _screenmap(5, 4, 1, stop_reason="budget"),
        "runs/20260612-9/crashes/crash-1.yaml": b"name: crash",
    }
    listed = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/").list_crawl_runs()
    assert [r["id"] for r in listed] == ["20260612-9", "20260610-1"]  # reverse-lexicographic
    newest, older = listed
    assert newest["screens"] == 5 and newest["transitions"] == 4 and newest["crashes"] == 1
    assert newest["crashFiles"] == ["crash-1.yaml"] and newest["stopReason"] == "budget"
    assert older["flowFiles"] == ["login.yaml"] and older["crashFiles"] == []


def test_list_crawl_runs_keys_on_screenmap_not_manifest() -> None:
    # A replay run writes manifest.json but no screenmap.json — it is not a crawl, so it must not
    # appear in the crawl history (the mirror of how list_runs keys on manifest.json).
    objects = {"runs/r1/manifest.json": b"{}", "runs/r2/screenmap.json": _screenmap(1, 0, 0)}
    listed = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/").list_crawl_runs()
    assert [r["id"] for r in listed] == ["r2"]


def test_list_crawl_runs_skips_unparseable_or_non_object_screenmap() -> None:
    objects = {
        "runs/bad/screenmap.json": b"not json",
        "runs/list/screenmap.json": b"[1, 2, 3]",  # a JSON array, not a screen map object
        "runs/good/screenmap.json": _screenmap(1, 0, 0),
    }
    listed = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/").list_crawl_runs()
    assert [r["id"] for r in listed] == ["good"]


def test_list_crawl_runs_lists_only_direct_yaml_children() -> None:
    # Only *.yaml directly under crashes/ becomes a link — a nested key or a non-yaml is skipped, so
    # the file list matches the local scan's `glob("*.yaml")` (no dead links).
    objects = {
        "runs/r1/screenmap.json": _screenmap(1, 0, 0),
        "runs/r1/crashes/a.yaml": b"x",
        "runs/r1/crashes/nested/b.yaml": b"x",
        "runs/r1/crashes/notes.txt": b"x",
    }
    (got,) = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/").list_crawl_runs()
    assert got["crashFiles"] == ["a.yaml"]


def test_list_crawl_runs_skips_keys_with_an_invalid_run_id() -> None:
    # A corrupt or hostile bucket could hold a key whose leading segment isn't a valid run id (a
    # `..` traversal, an empty segment). It must never reach the history list — the same containment
    # `archive`/`render_report` apply. list_keys returns keys verbatim, so the guard is on the id.
    objects = {
        "runs/../screenmap.json": _screenmap(1, 0, 0),
        "runs//screenmap.json": _screenmap(1, 0, 0),
        "runs/20260610-1/screenmap.json": _screenmap(2, 1, 0),
    }
    listed = ObjectStorageArtifactStore(FakeObjectStore(objects), prefix="runs/").list_crawl_runs()
    assert [r["id"] for r in listed] == ["20260610-1"]


def test_list_crawl_runs_is_scoped_to_the_stores_prefix() -> None:
    # Two orgs' crawls share one bucket under their own prefixes (the org prefix is baked into the
    # store instance); each store lists only its own runs — the tenant isolation BE-0190 relies on.
    objects = {
        "artifacts/acme-run/screenmap.json": _screenmap(1, 0, 0),
        "other/artifacts/beta-run/screenmap.json": _screenmap(2, 1, 0),
    }
    shared = FakeObjectStore(objects)
    default_org = ObjectStorageArtifactStore(shared, prefix="artifacts/")
    other_org = ObjectStorageArtifactStore(shared, prefix="other/artifacts/")
    assert [r["id"] for r in default_org.list_crawl_runs()] == ["acme-run"]
    assert [r["id"] for r in other_org.list_crawl_runs()] == ["beta-run"]

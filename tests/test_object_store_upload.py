"""Tests for the post-run evidence upload (BE-0110): `upload_tree` and `object_store_from_uri`.

`upload_tree` walks a finished ``runs/<id>/`` tree and uploads each file under the store prefix,
mirroring the local layout. It must never raise on a per-file failure — an upload error must not
change a run's already-final verdict. `object_store_from_uri` selects the backend and names the
exact install command when its SDK is missing.
"""

from __future__ import annotations

import mimetypes
import sys
from pathlib import Path

import pytest
from conftest import FakeObjectStore

from bajutsu.common.run_meta.object_store import (
    S3ObjectStore,
    StoreURI,
    content_type_for,
    evidence_target_from_uri,
    object_store_from_uri,
    upload_tree,
)


class _MemStore(FakeObjectStore):
    """An in-memory `ObjectStore` recording (key -> (bytes, content_type)) via put_file."""

    def __init__(self) -> None:
        super().__init__()
        self.uploads: dict[str, tuple[bytes, str]] = {}

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        self.uploads[key] = (path.read_bytes(), content_type)


def _run_tree(root: Path) -> Path:
    run = root / "20260702-143000"
    (run / "00-login" / "step-1").mkdir(parents=True)
    (run / "00-login" / "step-1" / "after.png").write_bytes(b"\x89PNG")
    (run / "manifest.json").write_text("{}")
    (run / "report.html").write_text("<html></html>")
    return run


def test_uploads_every_file_keyed_under_prefix_and_run_id(tmp_path: Path) -> None:
    run = _run_tree(tmp_path)
    store = _MemStore()

    summary = upload_tree(store, run, "evidence/main/")

    assert summary.uploaded == 3
    assert summary.failures == []
    assert set(store.uploads) == {
        "evidence/main/20260702-143000/00-login/step-1/after.png",
        "evidence/main/20260702-143000/manifest.json",
        "evidence/main/20260702-143000/report.html",
    }


def test_a_prefix_without_trailing_slash_is_normalized_not_fused(tmp_path: Path) -> None:
    # A non-normalized prefix must still nest under it, never fuse into `evidence/main<runId>/…`.
    run = _run_tree(tmp_path)
    store = _MemStore()
    upload_tree(store, run, "evidence/main")
    assert all(k.startswith("evidence/main/20260702-143000/") for k in store.uploads)


def test_infers_content_type_from_extension(tmp_path: Path) -> None:
    run = _run_tree(tmp_path)
    store = _MemStore()
    upload_tree(store, run, "")
    assert store.uploads["20260702-143000/00-login/step-1/after.png"][1] == "image/png"
    assert store.uploads["20260702-143000/manifest.json"][1] == "application/json"
    assert store.uploads["20260702-143000/report.html"][1] == "text/html"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("junit.xml", "application/xml"),
        ("console.log", "text/plain"),
        ("login.yaml", "application/yaml"),
        ("login.yml", "application/yaml"),
        ("after.png", "image/png"),
        ("manifest.json", "application/json"),
        ("report.html", "text/html"),
        ("capture.webm", "video/webm"),
        ("nothing.unknown", "application/octet-stream"),
    ],
)
def test_content_type_is_pinned_per_extension(name: str, expected: str) -> None:
    # The control plane signs this value into a presigned PUT URL and a worker on another machine
    # sends it back as Content-Type — a host-dependent answer is a 403 SignatureDoesNotMatch, not a
    # cosmetic drift. Pinning every extension the run tree produces is what makes the two agree.
    assert content_type_for(name) == expected


def test_content_type_never_consults_the_host_mime_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `mimetypes.guess_type` answers from the OS tables (/etc/mime.types and friends), which differ
    # between a full distro and a slim container. Blowing it up proves the helper never calls the
    # module-level lookup — but not that the `MimeTypes` instance it *does* call was built clean, so
    # `.deb` (a distro/Apache table knows it; the stdlib map does not) checks that too.
    def _boom(*_: object, **__: object) -> None:
        raise AssertionError("content_type_for must not use the host mimetypes database")

    monkeypatch.setattr(mimetypes, "guess_type", _boom)
    assert content_type_for("junit.xml") == "application/xml"
    assert content_type_for("after.png") == "image/png"
    assert content_type_for("pkg.deb") == "application/octet-stream"


def test_symlinks_are_skipped(tmp_path: Path) -> None:
    run = _run_tree(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("do not upload")
    (run / "link.txt").symlink_to(secret)
    store = _MemStore()

    summary = upload_tree(store, run, "")

    assert summary.uploaded == 3  # the symlink is not one of them
    assert not any(k.endswith("link.txt") for k in store.uploads)


def test_a_per_file_failure_is_collected_never_raised(tmp_path: Path) -> None:
    run = _run_tree(tmp_path)

    class _BrokenStore(FakeObjectStore):
        def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
            if key.endswith(".png"):
                raise OSError("network blip")

    summary = upload_tree(_BrokenStore(), run, "")

    assert summary.uploaded == 2
    assert len(summary.failures) == 1
    assert summary.failures[0][0].endswith("after.png")
    assert "network blip" in summary.failures[0][1]


def test_from_uri_builds_an_s3_store() -> None:
    store = object_store_from_uri(StoreURI(backend="s3", bucket="b", prefix=""))
    assert isinstance(store, S3ObjectStore)


def test_from_uri_names_the_install_command_when_the_sdk_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)  # force `import boto3` to raise
    with pytest.raises(ImportError, match="uv sync --extra s3"):
        object_store_from_uri(StoreURI(backend="s3", bucket="b", prefix=""))


def test_evidence_target_from_uri_builds_the_store_and_normalizes_the_prefix() -> None:
    # The serve control plane resolves --evidence-store to a credentialed store + normalized prefix.
    target = evidence_target_from_uri("s3://my-bucket/evidence")
    assert isinstance(target.store, S3ObjectStore)
    assert target.base_prefix == "evidence/"


def test_evidence_target_from_uri_rejects_a_malformed_uri() -> None:
    with pytest.raises(ValueError, match="s3://bucket/prefix or gs://bucket/prefix"):
        evidence_target_from_uri("not-a-uri")

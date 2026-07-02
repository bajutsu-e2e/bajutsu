"""Tests for the post-run evidence upload (BE-0110): `upload_tree` and `object_store_from_uri`.

`upload_tree` walks a finished ``runs/<id>/`` tree and uploads each file under the store prefix,
mirroring the local layout. It must never raise on a per-file failure — an upload error must not
change a run's already-final verdict. `object_store_from_uri` selects the backend and names the
exact install command when its SDK is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bajutsu.object_store import (
    S3ObjectStore,
    StoreURI,
    object_store_from_uri,
    upload_tree,
)


class _MemStore:
    """An in-memory `ObjectStore` recording (key -> (bytes, content_type)) via put_file."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        self.objects[key] = (path.read_bytes(), content_type)


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
    assert set(store.objects) == {
        "evidence/main/20260702-143000/00-login/step-1/after.png",
        "evidence/main/20260702-143000/manifest.json",
        "evidence/main/20260702-143000/report.html",
    }


def test_infers_content_type_from_extension(tmp_path: Path) -> None:
    run = _run_tree(tmp_path)
    store = _MemStore()
    upload_tree(store, run, "")
    assert store.objects["20260702-143000/00-login/step-1/after.png"][1] == "image/png"
    assert store.objects["20260702-143000/manifest.json"][1] == "application/json"
    assert store.objects["20260702-143000/report.html"][1] == "text/html"


def test_symlinks_are_skipped(tmp_path: Path) -> None:
    run = _run_tree(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("do not upload")
    (run / "link.txt").symlink_to(secret)
    store = _MemStore()

    summary = upload_tree(store, run, "")

    assert summary.uploaded == 3  # the symlink is not one of them
    assert not any(k.endswith("link.txt") for k in store.objects)


def test_a_per_file_failure_is_collected_never_raised(tmp_path: Path) -> None:
    run = _run_tree(tmp_path)

    class _BrokenStore:
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

"""Tests for the S3-compatible ObjectStore backing (BE-0015 server phase).

`S3ObjectStore` is the real backing for `ObjectStorageArtifactStore`'s injected `ObjectStore` slice:
an S3-compatible client (Cloudflare R2 / AWS S3 / MinIO) over one bucket. The boto3 client is
injected, so a small in-memory fake S3 (raising real botocore `ClientError`s for missing keys)
drives the contract here — no real bucket or network on the gate.
"""

from __future__ import annotations

import io

import pytest
from botocore.exceptions import ClientError

from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
from bajutsu.serve.server.object_store import S3ObjectStore, s3_client_from_env


class _FakeS3:
    """The slice of a boto3 S3 client S3ObjectStore uses, in memory."""

    def __init__(self, objects: dict[str, bytes], *, page: int = 1000) -> None:
        self._objects = objects
        self._page = page  # max keys per list_objects_v2 page, to exercise pagination

    @staticmethod
    def _missing(op: str) -> ClientError:
        return ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, op)

    def head_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
        if Key not in self._objects:
            raise self._missing("HeadObject")
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
        if Key not in self._objects:
            raise self._missing("GetObject")
        return {"Body": io.BytesIO(self._objects[Key])}

    def generate_presigned_url(self, op: str, Params: dict[str, str], ExpiresIn: int) -> str:  # noqa: N803
        return f"https://signed.example/{Params['Bucket']}/{Params['Key']}?exp={ExpiresIn}"

    def list_objects_v2(
        self,
        Bucket: str,  # noqa: N803
        Prefix: str,  # noqa: N803
        ContinuationToken: str | None = None,  # noqa: N803
    ) -> dict[str, object]:
        keys = sorted(k for k in self._objects if k.startswith(Prefix))
        start = int(ContinuationToken) if ContinuationToken else 0
        page = keys[start : start + self._page]
        nxt = start + self._page
        truncated = nxt < len(keys)
        return {
            "Contents": [{"Key": k} for k in page],
            "IsTruncated": truncated,
            "NextContinuationToken": str(nxt) if truncated else None,
        }


def test_exists_and_get_bytes() -> None:
    store = S3ObjectStore(_FakeS3({"r1/report.html": b"<html>"}), "bucket")
    assert store.exists("r1/report.html") is True
    assert store.exists("missing") is False
    assert store.get_bytes("r1/report.html") == b"<html>"
    assert store.get_bytes("missing") is None


def test_presigned_url_signs_a_time_limited_get() -> None:
    store = S3ObjectStore(_FakeS3({"k": b"x"}), "bkt", presign_ttl=60)
    url = store.presigned_url("k")
    assert url == "https://signed.example/bkt/k?exp=60"


def test_list_keys_paginates() -> None:
    objects = {f"runs/{i:02d}/manifest.json": b"{}" for i in range(5)}
    store = S3ObjectStore(_FakeS3(objects, page=2), "b")  # 2 per page -> 3 pages
    keys = store.list_keys("runs/")
    assert sorted(keys) == sorted(objects)


def test_unexpected_client_error_propagates() -> None:
    class _BrokenS3(_FakeS3):
        def head_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")

    store = S3ObjectStore(_BrokenS3({}), "b")
    with pytest.raises(ClientError):  # not a "missing" code -> surfaced, not swallowed as False
        store.exists("k")


def test_composes_with_the_artifact_store() -> None:
    # The real backing satisfies the ObjectStore slice the artifact store consumes.
    s3 = _FakeS3({"runs/r1/report.html": b"<html></html>"})
    art = ObjectStorageArtifactStore(S3ObjectStore(s3, "ignored"), prefix="runs/").get(
        "r1/report.html"
    )
    assert art is not None and art.redirect is not None and art.body is None


def test_s3_client_from_env_uses_endpoint_and_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAJUTSU_S3_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    client = s3_client_from_env()
    assert client.meta.endpoint_url == "https://acct.r2.cloudflarestorage.com"
    assert client.meta.region_name == "auto"

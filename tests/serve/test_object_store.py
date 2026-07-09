"""Tests for the S3-compatible ObjectStore backing (BE-0015 server phase).

`S3ObjectStore` is the real backing for `ObjectStorageArtifactStore`'s injected `ObjectStore` slice:
an S3-compatible client (Cloudflare R2 / AWS S3 / MinIO) over one bucket. The boto3 client is
injected, so a small in-memory fake S3 (raising real botocore `ClientError`s for missing keys)
drives the contract here — no real bucket or network on the gate. `object_store_from_env` (BE-0204)
also builds a `GCSObjectStore` from a `gs://` `BAJUTSU_SERVER_STORE`; a fake `storage.Client` keeps
that path off the network too.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from _shared import patch_gcs_client
from botocore.exceptions import ClientError

from bajutsu.object_store import GCSObjectStore
from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
from bajutsu.serve.server.object_store import S3ObjectStore, artifact_prefix, object_store_from_env


class _FakeS3:
    """The slice of a boto3 S3 client S3ObjectStore uses, in memory."""

    def __init__(self, objects: dict[str, bytes], *, page: int = 1000) -> None:
        self._objects = objects
        self._page = page  # max keys per list_objects_v2 page, to exercise pagination
        self.content_types: dict[str, str] = {}  # Key -> ContentType tagged on write

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

    def put_object(
        self,
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        Body: bytes,  # noqa: N803
        ContentType: str = "",  # noqa: N803
    ) -> dict[str, object]:
        self._objects[Key] = Body
        self.content_types[Key] = ContentType
        return {}

    def upload_file(
        self,
        Filename: str,  # noqa: N803
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        ExtraArgs: dict[str, str] | None = None,  # noqa: N803
    ) -> None:
        self._objects[Key] = Path(Filename).read_bytes()
        self.content_types[Key] = (ExtraArgs or {}).get("ContentType", "")

    def generate_presigned_url(self, op: str, Params: dict[str, str], ExpiresIn: int) -> str:  # noqa: N803
        ct = f"&ct={Params['ContentType']}" if "ContentType" in Params else ""
        return f"https://signed.example/{op}/{Params['Bucket']}/{Params['Key']}?exp={ExpiresIn}{ct}"

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


def test_put_bytes_writes_then_reads_back() -> None:
    fake = _FakeS3({})
    store = S3ObjectStore(fake, "bucket")
    store.put_bytes("scenarios/demo/smoke.yaml", b"- name: a\n")
    assert store.get_bytes("scenarios/demo/smoke.yaml") == b"- name: a\n"


def test_presigned_url_signs_a_time_limited_get() -> None:
    store = S3ObjectStore(_FakeS3({"k": b"x"}), "bkt", presign_ttl=60)
    url = store.presigned_url("k")
    assert url == "https://signed.example/get_object/bkt/k?exp=60"


def test_presigned_put_url_signs_a_time_limited_put() -> None:
    store = S3ObjectStore(_FakeS3({}), "bkt")
    url = store.presigned_put_url("k", content_type="image/png", ttl=120)
    assert url == "https://signed.example/put_object/bkt/k?exp=120&ct=image/png"


def test_put_bytes_and_put_file_tag_the_content_type(tmp_path: Path) -> None:
    fake = _FakeS3({})
    store = S3ObjectStore(fake, "bucket")
    store.put_bytes("scenarios/smoke.yaml", b"- a\n", content_type="text/yaml")
    src = tmp_path / "after.png"
    src.write_bytes(b"\x89PNG")
    store.put_file("r1/after.png", src, content_type="image/png")
    assert fake.content_types == {"scenarios/smoke.yaml": "text/yaml", "r1/after.png": "image/png"}


def test_list_keys_paginates() -> None:
    objects = {f"runs/{i:02d}/manifest.json": b"{}" for i in range(5)}
    store = S3ObjectStore(_FakeS3(objects, page=2), "b")  # 2 per page -> 3 pages
    keys = store.list_keys("runs/")
    assert sorted(keys) == sorted(objects)


@pytest.mark.parametrize("code", ["404", "NotFound", "NoSuchKey"])
def test_all_not_found_codes_map_to_absent(code: str) -> None:
    class _MissS3(_FakeS3):
        def head_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
            raise ClientError({"Error": {"Code": code}}, "HeadObject")

        def get_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
            raise ClientError({"Error": {"Code": code}}, "GetObject")

    store = S3ObjectStore(_MissS3({}), "b")
    assert store.exists("k") is False
    assert store.get_bytes("k") is None


def test_get_bytes_closes_the_streaming_body() -> None:
    # botocore's get_object body is a streaming body; it must be closed after reading so the HTTP
    # connection/fd isn't leaked under load.
    closed: list[bool] = []

    class _Body(io.BytesIO):
        def close(self) -> None:
            closed.append(True)
            super().close()

    class _StreamS3(_FakeS3):
        def get_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
            return {"Body": _Body(b"data")}

    assert S3ObjectStore(_StreamS3({"k": b"data"}), "b").get_bytes("k") == b"data"
    assert closed == [True]


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


def test_artifact_prefix_keys_under_base() -> None:
    assert artifact_prefix("") == "artifacts/"
    assert artifact_prefix("tenant/") == "tenant/artifacts/"


def test_object_store_from_env_needs_a_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_SERVER_STORE", raising=False)
    assert object_store_from_env() is None  # unset -> caller decides (require or skip)


def test_object_store_from_env_builds_an_s3_store_and_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt/tenant")
    monkeypatch.setenv("BAJUTSU_S3_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    result = object_store_from_env()
    assert result is not None
    store, prefix = result
    assert isinstance(store, S3ObjectStore)
    assert prefix == "tenant/"
    # BAJUTSU_S3_ENDPOINT/_REGION still reach the boto3 client through object_store_from_uri —
    # BAJUTSU_SERVER_STORE only replaced the bucket/prefix env vars, not these (BE-0204).
    client = store._client
    assert client.meta.endpoint_url == "https://acct.r2.cloudflarestorage.com"
    assert client.meta.region_name == "auto"


def test_object_store_from_env_rejects_a_malformed_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "bkt")  # no s3:// or gs:// scheme
    with pytest.raises(ValueError, match="BAJUTSU_SERVER_STORE 'bkt' is invalid"):
        object_store_from_env()


def test_object_store_from_env_builds_a_gcs_store_from_a_gs_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_gcs_client(monkeypatch)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "gs://bucket/tenant")
    result = object_store_from_env()
    assert result is not None
    store, prefix = result
    assert isinstance(store, GCSObjectStore)
    assert prefix == "tenant/"


def test_object_store_from_env_names_the_install_command_when_the_sdk_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setitem(sys.modules, "boto3", None)  # force `import boto3` to raise
    with pytest.raises(ImportError, match="uv sync --extra s3"):
        object_store_from_env()

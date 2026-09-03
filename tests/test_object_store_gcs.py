"""Tests for the GCS-backed ObjectStore (BE-0110).

`GCSObjectStore` wraps a ``google.cloud.storage.Bucket``; the bucket is injected (like
`S3ObjectStore`'s client), so a small in-memory fake drives the contract here — no real bucket,
credentials, or network, and the fake stands in for the SDK, so these never touch
``google.cloud.storage`` itself.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from bajutsu.common.run_meta.object_store import GCSObjectStore


class _FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self._store = store
        self.name = name
        self.signed: dict[str, Any] = {}

    def exists(self) -> bool:
        return self.name in self._store

    def download_as_bytes(self) -> bytes:
        return self._store[self.name]

    def upload_from_string(self, data: bytes, content_type: str = "") -> None:
        self._store[self.name] = data
        self._store[f"__ct__{self.name}"] = content_type.encode()

    def upload_from_filename(self, filename: str, content_type: str = "") -> None:
        self._store[self.name] = Path(filename).read_bytes()
        self._store[f"__ct__{self.name}"] = content_type.encode()

    def delete(self) -> None:
        # Real GCS raises on a missing blob (unlike S3's idempotent delete); model that so
        # `delete_key`'s delete-first/re-check-exists path is actually exercised.
        if self.name not in self._store:
            raise RuntimeError("404 No such object")
        self._store.pop(self.name, None)
        self._store.pop(f"__ct__{self.name}", None)

    def generate_signed_url(
        self,
        *,
        version: str,
        expiration: timedelta,
        method: str,
        content_type: str = "",
        service_account_email: str = "",
        access_token: str = "",
    ) -> str:
        secs = int(expiration.total_seconds())
        ct = f"&ct={content_type}" if content_type else ""
        sa = f"&sa={service_account_email}&tok={access_token}" if service_account_email else ""
        return f"https://signed.gcs/{self.name}?v={version}&m={method}&exp={secs}{ct}{sa}"


class _FakeBucket:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._store: dict[str, bytes] = dict(objects or {})

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self._store, name)

    def list_blobs(self, *, prefix: str) -> list[_FakeBlob]:
        return [
            _FakeBlob(self._store, k)
            for k in sorted(self._store)
            if k.startswith(prefix) and not k.startswith("__ct__")
        ]


def test_exists_and_get_bytes() -> None:
    store = GCSObjectStore(_FakeBucket({"r1/report.html": b"<html>"}))
    assert store.exists("r1/report.html") is True
    assert store.exists("missing") is False
    assert store.get_bytes("r1/report.html") == b"<html>"
    assert store.get_bytes("missing") is None


def test_put_bytes_round_trips_with_content_type() -> None:
    bucket = _FakeBucket()
    store = GCSObjectStore(bucket)
    store.put_bytes("scenarios/smoke.yaml", b"- name: a\n", content_type="text/yaml")
    assert store.get_bytes("scenarios/smoke.yaml") == b"- name: a\n"
    assert bucket._store["__ct__scenarios/smoke.yaml"] == b"text/yaml"


def test_put_file_streams_from_disk(tmp_path: Path) -> None:
    src = tmp_path / "after.png"
    src.write_bytes(b"\x89PNG")
    bucket = _FakeBucket()
    GCSObjectStore(bucket).put_file("r1/after.png", src, content_type="image/png")
    assert bucket._store["r1/after.png"] == b"\x89PNG"
    assert bucket._store["__ct__r1/after.png"] == b"image/png"


def test_presigned_urls_sign_get_and_put() -> None:
    store = GCSObjectStore(_FakeBucket({"k": b"x"}), presign_ttl=60)
    assert store.presigned_url("k") == "https://signed.gcs/k?v=v4&m=GET&exp=60"
    put = store.presigned_put_url("k", content_type="image/png", ttl=120)
    assert put == "https://signed.gcs/k?v=v4&m=PUT&exp=120&ct=image/png"


class _FakeWIFCredentials:
    """Stand-in for a Workload Identity Federation credential (KSA→GSA impersonation): it has an
    access token but no private key, so signing must go through IAM `signBlob` — the SDK does that
    when `generate_signed_url` is called with `service_account_email`/`access_token`."""

    def __init__(self) -> None:
        self.valid = False
        self.service_account_email: str | None = "gsa@project.iam.gserviceaccount.com"
        self.token = ""
        self.refresh_count = 0

    def refresh(self, request: Any) -> None:
        self.refresh_count += 1
        self.valid = True
        self.token = "fresh-token"


def test_presigned_url_signs_via_iam_when_credentials_injected() -> None:
    creds = _FakeWIFCredentials()
    store = GCSObjectStore(_FakeBucket({"k": b"x"}), presign_ttl=60, credentials=creds)
    url = store.presigned_url("k")
    assert url == (
        "https://signed.gcs/k?v=v4&m=GET&exp=60"
        "&sa=gsa@project.iam.gserviceaccount.com&tok=fresh-token"
    )
    assert creds.valid is True  # refreshed lazily before signing, not eagerly at construction
    assert creds.refresh_count == 1
    store.presigned_url("k")  # a still-valid token isn't refreshed again
    assert creds.refresh_count == 1


def test_presigned_url_signs_locally_when_credential_can_sign_itself() -> None:
    from google.auth.credentials import Signing

    class _FakeSigningCredentials(_FakeWIFCredentials, Signing):
        """Stand-in for a key-file or already-impersonated credential: it implements
        `google.auth.credentials.Signing`, so `_signing_kwargs` must leave it alone and let the
        SDK sign locally instead of routing through the IAM `signBlob` API."""

        def sign_bytes(self, message: bytes) -> bytes:
            raise NotImplementedError  # never called — generate_signed_url is faked below

        @property
        def signer_email(self) -> str | None:
            return self.service_account_email

        @property
        def signer(self) -> Any:
            raise NotImplementedError

    creds = _FakeSigningCredentials()
    store = GCSObjectStore(_FakeBucket({"k": b"x"}), presign_ttl=60, credentials=creds)
    assert store.presigned_url("k") == "https://signed.gcs/k?v=v4&m=GET&exp=60"
    assert creds.refresh_count == 0  # never refreshed — local signing needs no fresh token here


def test_presigned_url_signs_locally_when_credential_has_no_service_account_email() -> None:
    creds = _FakeWIFCredentials()
    creds.service_account_email = None
    store = GCSObjectStore(_FakeBucket({"k": b"x"}), presign_ttl=60, credentials=creds)
    # user ADC (`gcloud auth application-default login`) has no service account to sign as —
    # left alone, so the SDK raises its own error instead of an AttributeError from here.
    assert store.presigned_url("k") == "https://signed.gcs/k?v=v4&m=GET&exp=60"
    assert creds.refresh_count == 0


def test_list_keys_filters_by_prefix() -> None:
    bucket = _FakeBucket({f"runs/{i:02d}/manifest.json": b"{}" for i in range(3)})
    assert GCSObjectStore(bucket).list_keys("runs/") == [
        "runs/00/manifest.json",
        "runs/01/manifest.json",
        "runs/02/manifest.json",
    ]


def test_delete_key_removes_a_blob_and_is_idempotent() -> None:
    store = GCSObjectStore(_FakeBucket({"r1/report.html": b"<html>"}))
    store.delete_key("r1/report.html")
    assert store.exists("r1/report.html") is False
    store.delete_key("r1/report.html")  # already gone — no-op, no raise (BE-0239)


def test_delete_keys_removes_a_whole_run() -> None:
    store = GCSObjectStore(_FakeBucket({"r1/a.png": b"x", "r1/b.png": b"y", "r2/keep.png": b"z"}))
    store.delete_keys(store.list_keys("r1/"))
    assert store.list_keys("r1/") == []
    assert store.list_keys("r2/") == ["r2/keep.png"]  # a sibling run is untouched

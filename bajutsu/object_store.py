"""Object storage for run evidence: a backend-agnostic `ObjectStore` and a single-URI selector.

An evidence store is addressed by one URI — ``s3://bucket/prefix`` or ``gs://bucket/prefix`` —
so the destination (and thus the cloud lifecycle policy that governs retention) is a single,
greppable string (BE-0110). `parse_store_uri` splits it into a `StoreURI`; `object_store_from_uri`
builds the matching `ObjectStore`. Both S3 (boto3) and GCS (google-cloud-storage) SDKs are imported
**lazily**, so this module is safe to import without either extra and the default CLI/serve path
stays SDK-free (the #117 import guard).

`ObjectStore` and `S3ObjectStore` were promoted here from ``serve/server/object_store.py`` (which now
re-exports them) so both ``run`` and ``serve`` share one seam.
"""

from __future__ import annotations

import dataclasses
import mimetypes
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

_PRESIGN_TTL = 900  # seconds a signed GET URL stays valid (15 min)
_PUT_TTL = (
    3600  # seconds a signed PUT URL stays valid (1 h) — long enough to upload one run's batch
)
# S3/R2 error codes that mean "no such object" — treated as absent; anything else is surfaced.
_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})


class ObjectStore(Protocol):
    """The slice of an object-storage client the seams use (so a fake fits): artifact reads
    (exists / get_bytes / presigned_url / list_keys), writes (put_bytes / put_file), and signed PUT
    URLs (presigned_put_url) for credential-free uploads by a worker."""

    def exists(self, key: str) -> bool:
        """Whether an object exists at *key* (without downloading it)."""

    def get_bytes(self, key: str) -> bytes | None:
        """The object's bytes at *key*, or None if absent."""

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        """Write *data* to the object at *key* (creating or overwriting), tagging its MIME type when
        *content_type* is given."""

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        """Upload the file at *path* to *key*, streaming from disk (no full read into memory) — for
        large run artifacts like videos — tagging its MIME type when *content_type* is given."""

    def presigned_url(self, key: str) -> str:
        """A short-lived signed GET URL for *key*."""

    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = _PUT_TTL) -> str:
        """A signed PUT URL for *key*, valid *ttl* seconds — lets a caller upload without holding
        cloud credentials. Binds *content_type* when given, so the upload must match it."""

    def list_keys(self, prefix: str) -> list[str]:
        """Every object key under *prefix*."""


def _is_not_found(error: Any) -> bool:
    return str(error.response.get("Error", {}).get("Code", "")) in _NOT_FOUND


class S3ObjectStore:
    """`ObjectStore` over one S3-compatible bucket (AWS / R2 / MinIO) via an injected boto3 client."""

    def __init__(self, client: Any, bucket: str, *, presign_ttl: int = _PRESIGN_TTL) -> None:
        self._client = client
        self._bucket = bucket
        self._ttl = presign_ttl

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as e:
            if _is_not_found(e):
                return False
            raise  # a real error (auth, throttling, …) — don't mask it as "absent"
        return True

    def get_bytes(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as e:
            if _is_not_found(e):
                return None
            raise
        stream = resp["Body"]
        try:
            body: bytes = stream.read()
        finally:
            stream.close()  # release the HTTP connection/fd rather than leaking it under load
        return body

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        # upload_file streams from disk (multipart for large files) — no full read into memory.
        extra = {"ExtraArgs": {"ContentType": content_type}} if content_type else {}
        self._client.upload_file(str(path), self._bucket, key, **extra)

    def presigned_url(self, key: str) -> str:
        url: str = self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=self._ttl
        )
        return url

    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = _PUT_TTL) -> str:
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        url: str = self._client.generate_presigned_url("put_object", Params=params, ExpiresIn=ttl)
        return url

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kw)
            keys.extend(str(o["Key"]) for o in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                return keys
            token = resp.get("NextContinuationToken")


class GCSObjectStore:
    """`ObjectStore` over one Google Cloud Storage bucket via an injected `storage.Bucket`.

    The bucket is injected (like `S3ObjectStore`'s client) so a fake drives the gate. Signed URLs use
    V4 signing, which GCS supports for both GET and PUT."""

    def __init__(self, bucket: Any, *, presign_ttl: int = _PRESIGN_TTL) -> None:
        self._bucket = bucket
        self._ttl = presign_ttl

    def exists(self, key: str) -> bool:
        return bool(self._bucket.blob(key).exists())

    def get_bytes(self, key: str) -> bytes | None:
        blob = self._bucket.blob(key)
        if not blob.exists():
            return None
        data: bytes = blob.download_as_bytes()
        return data

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        kw = {"content_type": content_type} if content_type else {}
        self._bucket.blob(key).upload_from_string(data, **kw)

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        kw = {"content_type": content_type} if content_type else {}
        self._bucket.blob(key).upload_from_filename(str(path), **kw)

    def presigned_url(self, key: str) -> str:
        url: str = self._bucket.blob(key).generate_signed_url(
            version="v4", expiration=timedelta(seconds=self._ttl), method="GET"
        )
        return url

    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = _PUT_TTL) -> str:
        kw = {"content_type": content_type} if content_type else {}
        url: str = self._bucket.blob(key).generate_signed_url(
            version="v4", expiration=timedelta(seconds=ttl), method="PUT", **kw
        )
        return url

    def list_keys(self, prefix: str) -> list[str]:
        return [str(b.name) for b in self._bucket.list_blobs(prefix=prefix)]


@dataclasses.dataclass(frozen=True)
class StoreURI:
    """A parsed evidence-store URI: the backend, its bucket, and a key prefix.

    *prefix* is normalized to end with ``/`` (or be empty), so keys append cleanly without fusing
    (``prefix`` + ``run/x`` never yields ``prefixrun/x``)."""

    backend: Literal["s3", "gcs"]
    bucket: str
    prefix: str


# The URI scheme the caller writes → the backend name. ``gs`` follows the gsutil/gcloud convention
# while the backend name matches the library (google-cloud-storage → "gcs").
_SCHEME_BACKEND: dict[str, Literal["s3", "gcs"]] = {"s3": "s3", "gs": "gcs"}


def parse_store_uri(uri: str) -> StoreURI:
    """Parse an evidence-store URI (``s3://bucket/prefix`` or ``gs://bucket/prefix``) into a `StoreURI`.

    The first path segment is the bucket; the remainder is the key prefix, normalized to a trailing
    ``/`` (empty when the URI names only a bucket).

    Raises:
        ValueError: the scheme isn't ``s3`` / ``gs``, or the bucket is missing.
    """
    scheme, sep, rest = uri.partition("://")
    if not sep or scheme not in _SCHEME_BACKEND:
        raise ValueError(
            f"unsupported evidence-store URI {uri!r}: use s3://bucket/prefix or gs://bucket/prefix"
        )
    bucket, _, raw_prefix = rest.partition("/")
    if not bucket:
        raise ValueError(f"evidence-store URI {uri!r} is missing a bucket name")
    prefix = raw_prefix if (not raw_prefix or raw_prefix.endswith("/")) else raw_prefix + "/"
    return StoreURI(backend=_SCHEME_BACKEND[scheme], bucket=bucket, prefix=prefix)


def object_store_from_uri(uri: StoreURI) -> ObjectStore:
    """Build the `ObjectStore` a `StoreURI` names, using the standard credential chain of its SDK.

    Raises:
        ImportError: the backend's optional dependency isn't installed — the message names the exact
            ``uv sync --extra …`` to run.
    """
    if uri.backend == "s3":
        try:
            import boto3
        except ImportError as e:
            raise ImportError(
                "the s3:// evidence store needs boto3 — install it with `uv sync --extra s3`"
            ) from e
        client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("BAJUTSU_S3_ENDPOINT") or None,
            region_name=os.environ.get("BAJUTSU_S3_REGION") or os.environ.get("AWS_REGION") or None,
        )
        return S3ObjectStore(client, uri.bucket)
    try:
        from google.cloud import storage
    except ImportError as e:
        raise ImportError(
            "the gs:// evidence store needs google-cloud-storage — "
            "install it with `uv sync --extra gcs`"
        ) from e
    return GCSObjectStore(storage.Client().bucket(uri.bucket))


@dataclasses.dataclass(frozen=True)
class UploadSummary:
    """The outcome of an `upload_tree` walk: how many files went up, and any that failed.

    *failures* pairs each failed key with a short reason; it is empty on a clean upload. The upload
    never raises for a per-file error (BE-0110: an upload failure must not change the run verdict) —
    the caller inspects this to report."""

    uploaded: int
    failures: list[tuple[str, str]]


def _content_type(path: Path) -> str:
    """The MIME type inferred from *path*'s extension, defaulting to ``application/octet-stream``."""
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def upload_tree(store: ObjectStore, root: Path, prefix: str) -> UploadSummary:
    """Upload every file under *root* to *store*, keyed ``<prefix><root-name>/<relative-path>``.

    Mirrors the run's local directory layout under the prefix (so
    ``runs/<id>/00-login/after.png`` becomes ``<prefix><id>/00-login/after.png``). Symlinks and
    non-files are skipped (a symlink can't exfiltrate a path outside the tree), and each resolved
    path is confirmed inside *root* before upload. A per-file failure is collected into the returned
    `UploadSummary`, never raised — the run verdict is already final. Upload order is unspecified
    (the walk streams the generator rather than materializing/sorting it, so memory stays bounded on
    a large evidence tree) — order is irrelevant for a post-verdict side effect.
    """
    root = root.resolve()
    uploaded = 0
    failures: list[tuple[str, str]] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            continue
        rel = resolved.relative_to(root).as_posix()
        key = f"{prefix}{root.name}/{rel}"
        try:
            store.put_file(key, resolved, content_type=_content_type(resolved))
        except Exception as e:  # any upload error is reported, never fatal to the run
            failures.append((key, str(e)))
        else:
            uploaded += 1
    return UploadSummary(uploaded=uploaded, failures=failures)

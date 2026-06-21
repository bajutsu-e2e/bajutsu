"""An S3-compatible ObjectStore for the hosted backend (BE-0015 server phase).

`ObjectStorageArtifactStore` reads run artifacts through the injected `ObjectStore` slice
(``exists`` / ``get_bytes`` / ``presigned_url`` / ``list_keys``); this is its real backing: an
S3-compatible client over one bucket — Cloudflare R2 (the roadmap's choice), AWS S3, or MinIO,
which differ only in endpoint/credentials. The boto3 client is **injected** into `S3ObjectStore`
(so a fake drives the gate), and `s3_client_from_env` imports boto3 **lazily** — so this module is
safe to import without the ``server`` extra and the default path stays SDK-free (#117 import guard).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol


class ObjectStore(Protocol):
    """The slice of an S3-compatible client the server seams use (so a fake fits): artifact reads
    (exists / get_bytes / presigned_url / list_keys) plus scenario writes (put_bytes)."""

    def exists(self, key: str) -> bool:
        """Whether an object exists at *key* (without downloading it)."""

    def get_bytes(self, key: str) -> bytes | None:
        """The object's bytes at *key*, or None if absent."""

    def put_bytes(self, key: str, data: bytes) -> None:
        """Write *data* to the object at *key* (creating or overwriting)."""

    def put_file(self, key: str, path: Path) -> None:
        """Upload the file at *path* to *key*, streaming from disk (no full read into memory) — for
        large run artifacts like videos."""

    def presigned_url(self, key: str) -> str:
        """A short-lived signed GET URL for *key*."""

    def list_keys(self, prefix: str) -> list[str]:
        """Every object key under *prefix*."""


_PRESIGN_TTL = 900  # seconds a signed GET URL stays valid (15 min)
# S3/R2 error codes that mean "no such object" — treated as absent; anything else is surfaced.
_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})


def _is_not_found(error: Any) -> bool:
    return str(error.response.get("Error", {}).get("Code", "")) in _NOT_FOUND


class S3ObjectStore:
    """`ObjectStore` over one S3-compatible bucket via an injected boto3 S3 client."""

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

    def put_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def put_file(self, key: str, path: Path) -> None:
        # upload_file streams from disk (multipart for large files) — no full read into memory.
        self._client.upload_file(str(path), self._bucket, key)

    def presigned_url(self, key: str) -> str:
        url: str = self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=self._ttl
        )
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


def s3_client_from_env() -> Any:
    """A boto3 S3 client built from the environment (boto3 imported lazily — the ``server`` extra).

    ``BAJUTSU_S3_ENDPOINT`` points at R2/MinIO (unset for AWS S3); the region comes from
    ``BAJUTSU_S3_REGION`` / ``AWS_REGION`` (R2 uses ``auto``); credentials come from the standard
    AWS chain (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / profile / role)."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("BAJUTSU_S3_ENDPOINT") or None,
        region_name=os.environ.get("BAJUTSU_S3_REGION") or os.environ.get("AWS_REGION") or None,
    )


def s3_prefix() -> str:
    """The normalized tenant prefix from ``BAJUTSU_S3_PREFIX`` — trailing ``/`` when non-empty (so
    ``tenant`` doesn't fuse into ``tenantartifacts/``), empty when unset."""
    p = os.environ.get("BAJUTSU_S3_PREFIX", "")
    return p if (not p or p.endswith("/")) else p + "/"


def artifact_prefix(base: str = "") -> str:
    """The object-key prefix for run artifacts under *base*. Shared by the control plane's artifact
    store and the worker's upload so both agree on keys (``<base>artifacts/<runId>/…``)."""
    return f"{base}artifacts/"


# Keep in sync with config.DEFAULT_ORG; duplicated to avoid importing config on this hot path.
_DEFAULT_ORG = "default"


def org_prefix(base: str, org: str) -> str:
    """The per-org object-key prefix under *base* (BE-0015 multi-tenancy). The default org keeps
    *base* unchanged so the single-tenant layout is unaffected; every other org gets a ``<org>/``
    segment, isolating its artifacts/scenarios/baselines. Shared by the control plane and the worker
    so both agree on keys."""
    return base if org == _DEFAULT_ORG else f"{base}{org}/"


def object_store_from_env() -> S3ObjectStore | None:
    """An `S3ObjectStore` from the environment (``BAJUTSU_S3_BUCKET`` + endpoint/region), or None
    when no bucket is configured — so a caller can require it (control plane) or skip (a worker with
    no object storage)."""
    bucket = os.environ.get("BAJUTSU_S3_BUCKET")
    return S3ObjectStore(s3_client_from_env(), bucket) if bucket else None

"""Server-side object-storage helpers for the hosted backend (BE-0015 server phase).

`ObjectStorageArtifactStore` reads run artifacts through the injected `ObjectStore` slice
(``exists`` / ``get_bytes`` / ``presigned_url`` / ``list_keys``); its real backing is an
S3-compatible client over one bucket — Cloudflare R2 (the roadmap's choice), AWS S3, or MinIO,
which differ only in endpoint/credentials.

`ObjectStore` and `S3ObjectStore` were promoted to the top-level `bajutsu.object_store` (BE-0110) so
``run`` and ``serve`` share one seam; they are re-exported here for the existing server imports. This
module keeps the server-specific env/prefix helpers (`s3_client_from_env` builds the boto3 client
**lazily**, so importing it needs no ``server`` extra and the default path stays SDK-free — #117
import guard).
"""

from __future__ import annotations

import os
from typing import Any

from bajutsu.object_store import ObjectStore, S3ObjectStore

__all__ = [
    "ObjectStore",
    "S3ObjectStore",
    "artifact_prefix",
    "baseline_prefix",
    "object_store_from_env",
    "org_prefix",
    "s3_client_from_env",
    "s3_prefix",
    "scenario_prefix",
]


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


def scenario_prefix(base: str = "") -> str:
    """The object-key prefix for authored scenarios under *base* (``<base>scenarios/<app>/<ref>``).
    Shared by the control plane's scenario store and the URL the worker uploads a `record` job's
    authored scenario to, so both agree on keys (BE-0160)."""
    return f"{base}scenarios/"


def baseline_prefix(base: str = "") -> str:
    """The object-key prefix for visual baselines under *base* (``<base>baselines/<name>``). Shared
    by the control plane's baseline store and the presigned GET URLs it signs for the worker to
    download baselines before a run, so both agree on keys (BE-0160)."""
    return f"{base}baselines/"


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

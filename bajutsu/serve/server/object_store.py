"""Server-side object-storage helpers for the hosted backend (BE-0015 server phase).

`ObjectStorageArtifactStore` reads run artifacts through the injected `ObjectStore` slice
(``exists`` / ``get_bytes`` / ``presigned_url`` / ``list_keys``); its real backing is either an
S3-compatible bucket (Cloudflare R2, AWS S3, MinIO) or a Google Cloud Storage bucket, selected by
the single ``BAJUTSU_SERVER_STORE`` URI (BE-0204).

`ObjectStore`, `S3ObjectStore`, and `store_target_from_uri` live in the top-level
`bajutsu.object_store` (BE-0110) so ``run`` and ``serve`` share one seam; the first two are
re-exported here for the existing server imports. `object_store_from_env` (BE-0204) rebuilds the
server factory on that same URI machinery — `store_target_from_uri`, the "URI → (store, prefix)"
resolution ``--evidence-store``'s `evidence_target_from_uri` also builds on — instead of hand-rolling
an S3-only ``boto3`` client, so both cloud SDKs stay lazy imports and the default path stays
SDK-free (#117 import guard).
"""

from __future__ import annotations

import os

from bajutsu.object_store import ObjectStore, S3ObjectStore, store_target_from_uri

__all__ = [
    "ObjectStore",
    "S3ObjectStore",
    "artifact_prefix",
    "baseline_prefix",
    "object_store_from_env",
    "org_prefix",
    "scenario_prefix",
]


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


# Keep in sync with serve.orgs.DEFAULT_ORG; duplicated to avoid importing it on this hot path.
_DEFAULT_ORG = "default"


def org_prefix(base: str, org: str) -> str:
    """The per-org object-key prefix under *base* (BE-0015 multi-tenancy). The default org keeps
    *base* unchanged so the single-tenant layout is unaffected; every other org gets a ``<org>/``
    segment, isolating its artifacts/scenarios/baselines. Shared by the control plane and the worker
    so both agree on keys."""
    return base if org == _DEFAULT_ORG else f"{base}{org}/"


def object_store_from_env() -> tuple[ObjectStore, str] | None:
    """The (`ObjectStore`, key-prefix) pair ``BAJUTSU_SERVER_STORE`` names (``s3://bucket/prefix``
    or ``gs://bucket/prefix``), or None when unset — so a caller can require it (control plane) or
    skip (a worker with no object storage). The prefix already ends with ``/`` (or is empty).

    Raises:
        ValueError: ``BAJUTSU_SERVER_STORE`` is malformed (not a valid ``s3://`` / ``gs://`` URI).
        ImportError: the URI's backend SDK isn't installed (see `store_target_from_uri`).
    """
    uri = os.environ.get("BAJUTSU_SERVER_STORE")
    if not uri:
        return None
    try:
        return store_target_from_uri(uri)
    except ValueError as e:
        # parse_store_uri's message doesn't name a setting — reword it here so an operator sees
        # exactly which one to fix, rather than a generic "store URI" with no env var attached.
        raise ValueError(
            f"BAJUTSU_SERVER_STORE {uri!r} is invalid: use s3://bucket/prefix or gs://bucket/prefix"
        ) from e

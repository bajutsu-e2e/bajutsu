"""Tests for evidence-store URI parsing (BE-0110).

`parse_store_uri` turns a single ``s3://…`` / ``gs://…`` string into a `StoreURI`, so the destination
(and thus the cloud lifecycle policy) is one greppable value. These lock the split and normalization.
"""

from __future__ import annotations

import pytest

from bajutsu.object_store import StoreURI, parse_store_uri


def test_parses_s3_bucket_and_prefix() -> None:
    assert parse_store_uri("s3://my-bucket/evidence/main/") == StoreURI(
        backend="s3", bucket="my-bucket", prefix="evidence/main/"
    )


def test_gs_scheme_maps_to_the_gcs_backend() -> None:
    # The URI scheme follows gsutil/gcloud (gs://) while the backend name matches the library (gcs).
    assert parse_store_uri("gs://b/feature/pr-123/") == StoreURI(
        backend="gcs", bucket="b", prefix="feature/pr-123/"
    )


def test_prefix_without_trailing_slash_is_normalized() -> None:
    # A trailing slash is always present internally so keys append without fusing.
    assert parse_store_uri("s3://b/evidence/main").prefix == "evidence/main/"


def test_bucket_only_uri_has_an_empty_prefix() -> None:
    assert parse_store_uri("s3://b").prefix == ""
    assert parse_store_uri("s3://b/").prefix == ""


@pytest.mark.parametrize("uri", ["file:///tmp/x", "https://b/x", "b/x", "s3:/b/x", ""])
def test_unsupported_scheme_is_rejected(uri: str) -> None:
    with pytest.raises(ValueError, match="s3://bucket/prefix or gs://bucket/prefix"):
        parse_store_uri(uri)


def test_missing_bucket_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing a bucket"):
        parse_store_uri("s3:///evidence/")

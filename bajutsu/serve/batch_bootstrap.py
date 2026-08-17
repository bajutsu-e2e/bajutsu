"""Fill the batch-provider registry with the real AWS-backed concrete from the environment (BE-0336).

serve's fan-out resolves a cloud-batch job's provider by kind (`batch_provider.resolve`), but the
registry only holds a concrete once something registers one. This module is that step: at serve
startup it registers the real `DeviceFarmBatchProvider` — its boto3 client and presigned-URL transfer
wired to live AWS — when, and only when, the environment names a Device Farm project. With no project
named, `devicefarm` stays unregistered, so a mis-dispatched cloud-batch job fails loud at `resolve()`
rather than silently vanishing (fail-closed).

boto3 and urllib are reached only inside the factories below, so importing this module (and the
default `serve` path that imports it) stays SDK-free — the import guard that keeps `make serve`
single-process and the base install lean (test_import_guard.py).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from bajutsu.cloud.devicefarm import DeviceFarmClient, DeviceFarmError, Transfer
from bajutsu.serve import batch_provider

# Device Farm's control plane lives only in us-west-2; `DEVICEFARM_REGION` overrides it for a rare
# account pinned elsewhere. `DEVICEFARM_PROJECT_ARN` is the switch: its presence is what turns
# cloud-batch dispatch on for this serve process (AWS credentials come from boto3's own chain).
_PROJECT_ARN_ENV = "DEVICEFARM_PROJECT_ARN"
_REGION_ENV = "DEVICEFARM_REGION"
_DEFAULT_REGION = "us-west-2"


def _make_devicefarm_client(region: str) -> DeviceFarmClient:
    """Build the real boto3 ``devicefarm`` client (lazy import — the ``aws`` extra is optional)."""
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise DeviceFarmError(
            "cloud-batch dispatch needs boto3 — install it with `uv sync --extra aws`"
        ) from exc
    # boto3's dynamically built client is untyped, so present it as the DeviceFarmClient slice used.
    return cast("DeviceFarmClient", boto3.client("devicefarm", region_name=region))


class _HttpTransfer:
    """The real presigned-URL transfer over urllib (mirrors the CLI wrapper's, kept lazy here)."""

    def upload(self, url: str, path: Path) -> None:
        import urllib.request

        request = urllib.request.Request(url, data=path.read_bytes(), method="PUT")  # noqa: S310
        # An explicit timeout keeps a stalled S3 connection from hanging past the poll loops' cap.
        urllib.request.urlopen(request, timeout=300).close()  # noqa: S310 - Device Farm presigned https URL

    def download(self, url: str) -> bytes:
        import urllib.request

        with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310 - Device Farm presigned https URL
            payload: bytes = response.read()
        return payload


def bajutsu_source_root() -> Path | None:
    """The Bajutsu source tree's root — the directory holding `pyproject.toml`, `tests/`, and the
    `bajutsu/` package — when serve runs from a checkout, else None.

    The cloud-batch (Device Farm) package must be rooted here, not at the config's own directory:
    Device Farm's APPIUM_PYTHON_TEST_PACKAGE validation needs `tests/` at the package root and its
    test spec `pip install`s that root, both of which the config directory lacks. Returns None from an
    installed (non-checkout) Bajutsu — the tree carries no `pyproject.toml` there — so the caller
    falls back rather than packaging a source-less directory.
    """
    root = Path(__file__).resolve().parent.parent.parent
    return root if (root / "pyproject.toml").is_file() and (root / "tests").is_dir() else None


def register_batch_providers(env: Mapping[str, str] | None = None) -> list[str]:
    """Register concrete batch providers named by the environment; return the kinds registered.

    Device Farm registers only when `DEVICEFARM_PROJECT_ARN` is set, so a serve without AWS configured
    leaves the registry empty and a cloud-batch job dispatched by mistake fails loud at `resolve()`.

    Args:
        env: The environment to read (defaults to ``os.environ``); injected in tests.

    Returns:
        The provider kinds registered, in registration order — empty when none is configured.
    """
    resolved = os.environ if env is None else env
    project_arn = resolved.get(_PROJECT_ARN_ENV)
    if not project_arn:
        return []
    region = resolved.get(_REGION_ENV) or _DEFAULT_REGION
    provider = batch_provider.DeviceFarmBatchProvider(
        client=_make_devicefarm_client(region),
        transfer=cast("Transfer", _HttpTransfer()),
        project_arn=project_arn,
    )
    batch_provider.register("devicefarm", provider)
    return ["devicefarm"]

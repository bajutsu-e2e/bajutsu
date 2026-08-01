"""Tests for the batch-cloud provider seam serve dispatches a cloud-batch job through (BE-0336).

The registry is provider-generic (fail-closed on an unknown kind); the Device Farm concrete is driven
against an in-memory fake client/transfer — no real AWS — so its packaging + single-device selection +
collect logic is exercised without the ``aws`` extra.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bajutsu.serve import batch_provider as bp


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    # The registry is process-global; snapshot and restore it so a registration in one test never
    # leaks into another (mirrors the serve env-snapshot fixtures).
    saved = dict(bp._PROVIDERS)
    try:
        yield
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)


def test_resolve_returns_the_registered_provider() -> None:
    sentinel: Any = object()
    bp.register("df", sentinel)
    assert bp.resolve("df") is sentinel


def test_resolve_fails_closed_on_an_unknown_provider() -> None:
    # An unknown provider is a clean config error raised here, not a silent no-op that would let a
    # cloud-batch job quietly vanish.
    with pytest.raises(ValueError, match="unknown batch provider 'nope'"):
        bp.resolve("nope")


def _zip_bytes(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


class _FakeClient:
    """A minimal in-memory Device Farm client that drives the happy path and records the schedule."""

    def __init__(self) -> None:
        self.scheduled: dict[str, Any] | None = None

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        return {"upload": {"arn": f"arn:upload/{name}", "url": f"https://s3/{name}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        return {"upload": {"arn": arn, "status": "SUCCEEDED"}}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        self.scheduled = kwargs
        return {"run": {"arn": "arn:run/1"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn, "status": "COMPLETED"}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {"artifacts": [{"name": "runs", "extension": "zip", "url": "https://s3/runs.zip"}]}


class _FakeTransfer:
    def __init__(self, *, manifest_ok: bool) -> None:
        self.uploaded: list[str] = []
        self._ok = manifest_ok

    def upload(self, url: str, path: Path) -> None:
        self.uploaded.append(url)

    def download(self, url: str) -> bytes:
        manifest = json.dumps(
            {"ok": self._ok, "scenarios": [{"scenario": "alpha", "ok": self._ok}]}
        )
        return _zip_bytes({"runs/20260101-1/manifest.json": manifest})


def test_devicefarm_provider_submits_one_scenario_reserving_a_single_device(tmp_path: Path) -> None:
    # The Device Farm concrete renders a one-scenario spec, packages the project, and schedules the run
    # with deviceSelectionConfiguration + maxDevices:1 (never a static pool), then reports the verdict
    # from the downloaded manifest tree.
    client = _FakeClient()
    transfer = _FakeTransfer(manifest_ok=True)
    provider = bp.DeviceFarmBatchProvider(
        client=client, transfer=transfer, project_arn="arn:project/1", sleep=lambda _: None
    )
    work = tmp_path / "project"
    work.mkdir()
    (work / "smoke.yaml").write_text("- name: alpha\n  steps: []\n", encoding="utf-8")
    (tmp_path / "app.apk").write_bytes(b"apk")
    request = bp.BatchRequest(
        provider="devicefarm",
        scenario="smoke.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path=str(tmp_path / "app.apk"),
    )
    dest = tmp_path / "download"

    verdict = provider.submit(request, work_dir=work, dest=dest)

    assert verdict.ok and verdict.passed == 1
    assert len(transfer.uploaded) == 3  # app, test package, and test spec
    assert client.scheduled is not None
    assert client.scheduled["deviceSelectionConfiguration"]["maxDevices"] == 1
    assert "devicePoolArn" not in client.scheduled
    assert (dest / "runs" / "20260101-1" / "manifest.json").exists()

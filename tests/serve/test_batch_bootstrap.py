"""Tests for serve's batch-provider bootstrap: register the Device Farm concrete from the env (BE-0336).

serve's fan-out resolves a cloud-batch job's provider by kind; this bootstrap is what fills the
registry with the real AWS-backed ``devicefarm`` provider when — and only when — the environment names
a Device Farm project. The boto3 client and presigned-URL transfer are monkeypatched here so the
wiring is exercised without the ``aws`` extra or any real AWS call.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from bajutsu.serve import batch_bootstrap
from bajutsu.serve import batch_provider as bp


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    # The registry is process-global; snapshot and restore it so a registration in one test never
    # leaks into another (mirrors test_batch_provider.py and the serve env-snapshot fixtures).
    saved = dict(bp._PROVIDERS)
    try:
        yield
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)


def test_source_root_points_at_the_checkout_root() -> None:
    # Serving from this checkout, the Device Farm package root is the source tree that holds
    # pyproject.toml + tests/ (what the test spec `pip install`s and Device Farm validates), not the
    # config's own directory. The check runs against the real tree — no mock — since that is the exact
    # thing being asserted.
    root = batch_bootstrap.bajutsu_source_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "tests").is_dir()
    assert (root / "bajutsu").is_dir()


def _recording_client(regions: list[str]) -> Callable[[str], object]:
    """A `_make_devicefarm_client` stand-in that records the region it was asked for."""

    def make(region: str) -> object:
        regions.append(region)
        return object()

    return make


def test_registers_devicefarm_when_project_arn_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    regions: list[str] = []
    monkeypatch.setattr(
        batch_bootstrap,
        "_make_devicefarm_client",
        _recording_client(regions),
    )
    monkeypatch.setattr(batch_bootstrap, "HttpTransfer", object)

    # The ARN embeds a *different* region on purpose: the default must come from the code, not be
    # parsed back out of the ARN, so an ARN naming ap-northeast-1 must still reach the client as
    # us-west-2 (Device Farm's control plane lives only there).
    registered = batch_bootstrap.register_batch_providers(
        {"DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:ap-northeast-1:1:project:abc"}
    )

    assert registered == ["devicefarm"]
    provider = bp.resolve("devicefarm")
    assert isinstance(provider, bp.DeviceFarmBatchProvider)
    assert provider._project_arn == "arn:aws:devicefarm:ap-northeast-1:1:project:abc"
    assert regions == ["us-west-2"]


def test_region_override_reaches_the_client_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    regions: list[str] = []
    monkeypatch.setattr(
        batch_bootstrap,
        "_make_devicefarm_client",
        _recording_client(regions),
    )
    monkeypatch.setattr(batch_bootstrap, "HttpTransfer", object)

    # ARN region and the override differ, so the region reaching the client must be DEVICEFARM_REGION,
    # not the one embedded in the ARN.
    batch_bootstrap.register_batch_providers(
        {
            "DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-east-1:1:project:abc",
            "DEVICEFARM_REGION": "eu-west-1",
        }
    )

    assert regions == ["eu-west-1"]


def test_no_devicefarm_without_project_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    # No project ARN → nothing registered, so a mis-dispatched cloud-batch job fails loud at resolve()
    # rather than silently vanishing (fail-closed, prime directive 2).
    def _boom(_region: str) -> object:
        raise AssertionError("the client factory must not run without a project ARN")

    monkeypatch.setattr(batch_bootstrap, "_make_devicefarm_client", _boom)

    registered = batch_bootstrap.register_batch_providers({})

    assert registered == []
    with pytest.raises(ValueError, match="unknown batch provider 'devicefarm'"):
        bp.resolve("devicefarm")


def test_register_batch_providers_raises_when_client_factory_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `register_batch_providers` propagates a client-factory failure (missing boto3, bad credentials)
    # so the serve() caller can catch it and keep the process running rather than dying at boot.
    from bajutsu.common.cloud.devicefarm import DeviceFarmError

    monkeypatch.setattr(
        batch_bootstrap,
        "_make_devicefarm_client",
        lambda _region: (_ for _ in ()).throw(DeviceFarmError("cloud-batch dispatch needs boto3")),
    )
    monkeypatch.setattr(batch_bootstrap, "HttpTransfer", object)

    with pytest.raises(DeviceFarmError, match="boto3"):
        batch_bootstrap.register_batch_providers(
            {"DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-west-2:1:project:abc"}
        )

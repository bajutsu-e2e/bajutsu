"""Tests for the device-provider seam and registry (bajutsu/runner/device_provider.py, BE-0236).

The `run` pipeline resolves where its devices come from through a `DeviceProvider` seam, keyed by a
transport `kind` in a registry that mirrors the mailbox registry (BE-0186). These tests cover the
built-in `local` provider (the udid passes through unchanged, nothing to release), fail-closed
resolution of an unknown `kind`, that the registry is a real extension point, and the
`ProvisionProfile` a lease carries — all pure, no device, no LLM (the seam is off the verdict path).
"""

from __future__ import annotations

import pytest

from bajutsu.config import AndroidConfig, DeviceProvider, Effective
from bajutsu.platform_lifecycle import ProvisionProfile
from bajutsu.runner import device_provider as dp
from bajutsu.scenario import Redact


def _eff(*, device_provider: DeviceProvider | None = None) -> Effective:
    return Effective(
        target="app",
        platform_config=AndroidConfig(package="com.example.app"),
        backend=["android"],
        device="booted",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
        device_provider=device_provider,
    )


def test_local_provider_passes_the_udid_through() -> None:
    # No deviceProvider = the built-in local provider: the requested udid is the udid the run resolves
    # against, the ProvisionProfile is inert (a locally-attached device boots and installs as before),
    # and release is a no-op (nothing was reserved).
    lease = dp.acquire_device(_eff(), "emulator-5554,emulator-5556")
    assert lease.udid_spec == "emulator-5554,emulator-5556"
    assert lease.provision == ProvisionProfile()
    lease.release()  # no-op, must not raise


def test_explicit_local_kind_resolves_the_same_provider() -> None:
    lease = dp.acquire_device(_eff(device_provider=DeviceProvider(kind="local")), "booted")
    assert lease.udid_spec == "booted"
    assert lease.provision == ProvisionProfile()


def test_unknown_provider_kind_fails_closed() -> None:
    # Fail-closed at resolution (like the mailbox registry), never a silent fallback to local.
    with pytest.raises(ValueError, match="unknown device provider 'firebase-streaming'"):
        dp.acquire_device(_eff(device_provider=DeviceProvider(kind="firebase-streaming")), "booted")


def test_registry_is_a_real_extension_point() -> None:
    """Register a fake cloud provider, resolve it, then remove it (global registry)."""

    class _FakeCloud:
        def acquire(self, eff: Effective, requested_udid: str) -> dp.DeviceLease:
            # A cloud provider reserves its own device and hands back its endpoint, ready to drive.
            return dp.DeviceLease(
                udid_spec="10.0.0.1:5555",
                provision=ProvisionProfile(boot_ready=True, app_preinstalled=True),
                release=lambda: None,
            )

    dp.register("fake-cloud", _FakeCloud())
    try:
        lease = dp.acquire_device(_eff(device_provider=DeviceProvider(kind="fake-cloud")), "booted")
        assert lease.udid_spec == "10.0.0.1:5555"  # the reserved endpoint, not the --udid flag
        assert lease.provision == ProvisionProfile(boot_ready=True, app_preinstalled=True)
    finally:
        dp._PROVIDERS.pop("fake-cloud", None)


def test_release_runs_the_lease_teardown() -> None:
    # A provider's release closes over whatever it reserved; the run calls it in a finally, so the
    # device is returned even on failure. Assert the callable is invoked when the run releases.
    released: list[bool] = []

    class _CountingCloud:
        def acquire(self, eff: Effective, requested_udid: str) -> dp.DeviceLease:
            return dp.DeviceLease(
                udid_spec="x", provision=ProvisionProfile(), release=lambda: released.append(True)
            )

    dp.register("counting", _CountingCloud())
    try:
        lease = dp.acquire_device(_eff(device_provider=DeviceProvider(kind="counting")), "booted")
        lease.release()
        assert released == [True]
    finally:
        dp._PROVIDERS.pop("counting", None)

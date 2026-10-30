"""The built-in `devicefarm-host` provider: the device a Device Farm host reserved (BE-0465)."""

from __future__ import annotations

from bajutsu.common.config import Effective
from bajutsu.common.platform_lifecycle import ProvisionProfile

from .device_lease import DeviceLease


class _DevicefarmHostProvider:
    """The device of the run a Device Farm package carries, on the Device Farm host (BE-0465).

    Device Farm reserves the device and installs the run's uploaded app before the test spec starts,
    on Android and iOS alike, so the profile reports it booted with its build in place: the run
    launches that build rather than installing `subject.path` itself. The requested udid (`booted`, or the
    reserved iPhone's id) passes through, and the reservation is Device Farm's, so there is nothing to
    release.
    """

    def acquire(self, eff: Effective, requested_udid: str) -> DeviceLease:  # noqa: ARG002  # DeviceProvider shape
        return DeviceLease(
            udid_spec=requested_udid,
            provision=ProvisionProfile(boot_ready=True, app_preinstalled=True),
        )

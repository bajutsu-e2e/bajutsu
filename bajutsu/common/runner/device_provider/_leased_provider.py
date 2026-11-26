"""The built-in `leased` provider: a device a lease broker lends (BE-0511)."""

from __future__ import annotations

from bajutsu.common.config import Effective
from bajutsu.common.platform_lifecycle import ProvisionProfile

from .device_lease import DeviceLease
from .lease_command import borrow_device, declared_models
from .tagged_udid import entries_for_platform


class _LeasedProvider:
    """Borrow one device from the broker, or take the ones a caller already borrowed.

    A worker borrows for its job and passes the addresses as `--udid`, so the run it spawns borrows
    nothing and releases nothing. A run given no `--udid` borrows for itself. The profile is inert:
    the run boots and installs on a borrowed device as on a local one.
    """

    def acquire(self, eff: Effective, requested_udid: str) -> DeviceLease:
        if requested_udid and requested_udid != "booted":
            return DeviceLease(
                udid_spec=entries_for_platform(requested_udid, eff.platform),
                provision=ProvisionProfile(),
            )
        config = eff.device_provider
        if (
            config is None
            or not config.command
            or config.acquire_timeout is None
            or config.release_timeout is None
        ):
            raise ValueError(
                "device provider 'leased' needs a lease command: name one under `lease:` in a "
                "`leased` environment of worker.yaml"
            )
        # A run takes one device here, so a list of models is answered by its first.
        models = declared_models(eff)
        borrowed = borrow_device(
            config.command,
            platform=eff.platform,
            device=models[0] if models else None,
            acquire_timeout=config.acquire_timeout,
            release_timeout=config.release_timeout,
        )
        return DeviceLease(
            udid_spec=borrowed.address,
            provision=ProvisionProfile(),
            release=borrowed.release,
            env=borrowed.env,
        )

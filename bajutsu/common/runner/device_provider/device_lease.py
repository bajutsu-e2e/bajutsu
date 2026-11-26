"""One provider's answer for a run: which devices to drive, and how to release them (BE-0236)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field

from bajutsu.common.platform_lifecycle import ProvisionProfile


@dataclass(frozen=True)
class DeviceLease:
    """One provider's answer for a run: which device(s) to drive and how to release them (BE-0236).

    `udid_spec` is the string the run resolves its lanes against — for the local provider the `--udid`
    flag verbatim (a comma list of concrete devices, or `booted`), for a cloud provider the reserved
    device's serial / endpoint. `provision` records what the provider already did (see
    `ProvisionProfile`), and `release` returns the device to the provider (a no-op for a
    locally-attached one); the run calls it in a finally, so a reserved device is freed even on failure.
    `env` holds the variables the run's device tools must see to reach the device (BE-0511).
    """

    udid_spec: str
    provision: ProvisionProfile
    # A frozen dataclass may hold a callable field; the run invokes it to hand the device back.
    release: Callable[[], None] = lambda: None
    env: Mapping[str, str] = field(default_factory=dict)


class LeaseEnvConflictError(ValueError):
    """Two leases need different values for one variable, which one process cannot hold."""


def merge_lease_env(into: MutableMapping[str, str], env: Mapping[str, str]) -> None:
    """Add *env* to *into*, the environment the leases of one run share.

    Raises:
        LeaseEnvConflictError: *env* gives a variable another lease set a different value.
    """
    for key, value in env.items():
        if into.setdefault(key, value) != value:
            raise LeaseEnvConflictError(
                f"two device leases need different values for {key}, and one run has one "
                "environment"
            )

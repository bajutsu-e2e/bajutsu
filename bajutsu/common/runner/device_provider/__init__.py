"""Acquire the device(s) a run drives, via a provider registry keyed on `kind` (BE-0236).

A platform is a backend; BE-0236 makes *where the devices come from* the same kind of seam. A
`DeviceProvider` resolves a target's `deviceProvider.kind` into a `DeviceLease`: the udid spec the
run resolves its lanes against, a `ProvisionProfile` recording what the provider already did to the
device (booted it, installed the app), and a `release` to hand the device back. The registry mirrors
the mailbox transport registry (BE-0186): the built-in `local` provider passes the `--udid` string
through unchanged (today's locally-attached path, byte-for-byte), and an unknown `kind` fails closed
when the run resolves it. The seam sits upstream of the device pool and entirely off the run/CI
verdict path — no LLM, no assertion input (prime directive 1). It ships the `local` reference provider
the `appium` live path (a reserved iOS device behind an Appium / WebDriver endpoint, BE-0238), and
`leased`, which borrows a device from a lease broker through its command (BE-0511); a
further device-cloud adapter registers its own `kind` (a sibling item), never a branch here.
"""

from ._appium_provider import _AppiumProvider as _AppiumProvider
from ._functions import _PROVIDERS as _PROVIDERS
from ._functions import _ensure_builtins as _ensure_builtins
from ._functions import acquire_device, register
from ._local_provider import _LocalProvider as _LocalProvider
from .device_lease import DeviceLease, LeaseEnvConflictError, merge_lease_env
from .device_provider import DeviceProvider

__all__ = [
    "DeviceLease",
    "DeviceProvider",
    "LeaseEnvConflictError",
    "acquire_device",
    "merge_lease_env",
    "register",
]

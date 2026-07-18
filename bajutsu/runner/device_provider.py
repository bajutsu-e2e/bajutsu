"""Acquire the device(s) a run drives, via a provider registry keyed on `kind` (BE-0236).

A platform is a backend; BE-0236 makes *where the devices come from* the same kind of seam. A
`DeviceProvider` resolves a target's `deviceProvider.kind` into a `DeviceLease`: the udid spec the
run resolves its lanes against, a `ProvisionProfile` recording what the provider already did to the
device (booted it, installed the app), and a `release` to hand the device back. The registry mirrors
the mailbox transport registry (BE-0186): the built-in `local` provider passes the `--udid` string
through unchanged (today's locally-attached path, byte-for-byte), and an unknown `kind` fails closed
when the run resolves it. The seam sits upstream of the device pool and entirely off the run/CI
verdict path — no LLM, no assertion input (prime directive 1). This ships only the `local` reference
provider; a device-cloud adapter registers its own `kind` (a sibling item), never a branch here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from bajutsu.config import Effective
from bajutsu.platform_lifecycle import ProvisionProfile


@dataclass(frozen=True)
class DeviceLease:
    """One provider's answer for a run: which device(s) to drive and how to release them (BE-0236).

    `udid_spec` is the string the run resolves its lanes against — for the local provider the `--udid`
    flag verbatim (a comma list of concrete devices, or `booted`), for a cloud provider the reserved
    device's serial / endpoint. `provision` records what the provider already did (see
    `ProvisionProfile`), and `release` returns the device to the provider (a no-op for a
    locally-attached one); the run calls it in a finally, so a reserved device is freed even on failure.
    """

    udid_spec: str
    provision: ProvisionProfile
    # A frozen dataclass may hold a callable field; the run invokes it to hand the device back.
    release: Callable[[], None] = lambda: None


class DeviceProvider(Protocol):
    """Reserve the device(s) for a run and hand back a `DeviceLease` (BE-0236).

    Off the verdict path: a provider decides *where* the run's devices come from, never whether a
    step passes. `acquire` is called once per run, upstream of the device pool; the returned lease's
    `release` is called once when the run finishes.
    """

    def acquire(self, eff: Effective, requested_udid: str) -> DeviceLease: ...


class _LocalProvider:
    """The built-in `local` provider: today's locally-attached path, unchanged.

    The `--udid` string passes straight through as the udid spec, the profile is inert (a
    locally-attached device boots and installs the app itself), and there is nothing to release.
    """

    def acquire(self, eff: Effective, requested_udid: str) -> DeviceLease:
        return DeviceLease(udid_spec=requested_udid, provision=ProvisionProfile())


_PROVIDERS: dict[str, DeviceProvider] = {}


def register(kind: str, provider: DeviceProvider) -> None:
    """Register *provider* under *kind* (idempotent — a later call overrides)."""
    _PROVIDERS[kind] = provider


def _ensure_builtins() -> None:
    """Register the built-in `local` provider on first use (`setdefault` leaves a test override intact)."""
    _PROVIDERS.setdefault("local", _LocalProvider())


def acquire_device(eff: Effective, requested_udid: str) -> DeviceLease:
    """The `DeviceLease` for this target's configured provider (default `local`).

    Resolves `eff.device_provider.kind` against the registry — BE-0236's single fail-closed point,
    mirroring the mailbox registry: an unknown `kind` raises here (a clean config error) rather than
    silently falling back to local. A target with no `deviceProvider` uses `local`.

    Raises:
        ValueError: the configured `kind` has no registered provider.
    """
    _ensure_builtins()
    kind = eff.device_provider.kind if eff.device_provider is not None else "local"
    if kind not in _PROVIDERS:
        allowed = ", ".join(repr(k) for k in _PROVIDERS)
        raise ValueError(f"unknown device provider {kind!r}: registered kinds are {allowed}")
    return _PROVIDERS[kind].acquire(eff, requested_udid)

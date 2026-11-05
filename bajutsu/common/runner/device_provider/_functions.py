"""The provider registry: register a kind, and resolve a target's configured one."""

from __future__ import annotations

from bajutsu.common.config import Effective

from ._appium_provider import _AppiumProvider
from ._devicefarm_host_provider import _DevicefarmHostProvider
from ._local_provider import _LocalProvider
from .device_lease import DeviceLease
from .device_provider import DeviceProvider

_PROVIDERS: dict[str, DeviceProvider] = {}


def register(kind: str, provider: DeviceProvider) -> None:
    """Register *provider* under *kind* (idempotent — a later call overrides)."""
    _PROVIDERS[kind] = provider


def _ensure_builtins() -> None:
    """Register the built-in providers on first use (`setdefault` leaves a test override intact)."""
    _PROVIDERS.setdefault("local", _LocalProvider())
    _PROVIDERS.setdefault("appium", _AppiumProvider())
    _PROVIDERS.setdefault("devicefarm-host", _DevicefarmHostProvider())


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

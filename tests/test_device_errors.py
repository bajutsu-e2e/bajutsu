"""The platform-neutral `DeviceError` base and the backend subclasses under it (BE-0260)."""

from __future__ import annotations

import pytest

from bajutsu import adb, device_errors, simctl


def test_backend_errors_share_the_neutral_base() -> None:
    assert issubclass(simctl.DeviceError, device_errors.DeviceError)
    assert issubclass(adb.DeviceError, device_errors.DeviceError)


def test_backend_errors_are_siblings_not_a_chain() -> None:
    # adb no longer subclasses the iOS error — the dependency inversion BE-0260 removes, so a
    # generic handler need not name the iOS backend to catch a generic device fault.
    assert not issubclass(adb.DeviceError, simctl.DeviceError)
    assert not issubclass(simctl.DeviceError, adb.DeviceError)


def test_generic_handler_catches_either_backend() -> None:
    for err in (simctl.DeviceError("ios fault"), adb.DeviceError("android fault")):
        with pytest.raises(device_errors.DeviceError):
            raise err

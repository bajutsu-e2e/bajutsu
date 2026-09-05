"""The platform-neutral `DeviceError` base and the backend subclasses under it (BE-0260)."""

from __future__ import annotations

import pytest

from bajutsu.common.backend_cli import adb, simctl
from bajutsu.common.devices import errors as device_errors


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


def test_the_ios_timeout_keeps_every_type_it_had_and_gains_the_neutral_one() -> None:
    # BE-0374 adds `device_errors.DeviceTimeout` as a *second* base, never as a replacement: every
    # handler written against the iOS types — including the deliberate suppressions BE-0363 audited
    # one by one — must keep matching, or a wedge changes route somewhere nobody looked.
    exc = simctl.DeviceTimeout("device operation timed out after 60s: xcrun simctl shutdown X")
    assert isinstance(exc, simctl.DeviceError)
    assert isinstance(exc, device_errors.DeviceError)
    assert isinstance(exc, device_errors.DeviceTimeout)


def test_a_device_that_refused_is_not_a_timeout() -> None:
    # The distinction the pipeline branches on: a refusal is evidence about one operation, a timeout
    # is evidence the service behind every operation stopped answering. A backend with no timeout
    # type of its own yet (adb) must never be mistaken for the latter.
    assert not isinstance(simctl.DeviceError("erase refused"), device_errors.DeviceTimeout)
    assert not isinstance(adb.DeviceError("android fault"), device_errors.DeviceTimeout)

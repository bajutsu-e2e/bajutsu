"""Tests for the XCUITest environment's target selection (BE-0238 Unit 1).

BE-0019 drove only the Simulator; BE-0238 generalises the same `xcodebuild test-without-building`
driving layer to a real iOS device, where the only difference is the `-destination` platform and
that the simctl device-prep (erase / boot / install / permissions) does not apply. The Simulator's
own boot path still needs Xcode, so it stays off the gate; here the target-selection logic and the
loud refusal of simctl-only operations against a real device are exercised without one — the
`xcodebuild`/toolchain boundary is the sanctioned fake point (the same one BE-0019 fakes).
"""

from __future__ import annotations

import plistlib
import subprocess
from typing import Any

import pytest

from bajutsu import backends, simctl
from bajutsu.config import Effective, load_config, resolve
from bajutsu.platform_lifecycle.environments.xcuitest import (
    XcuitestEnvironment,
    _destination,
)
from bajutsu.scenario import Preconditions

_DEVICE_UDID = "00008030-000A1B2C3D4E"  # a physical-device id shape (not a simctl UUID)


def _device_eff(*, app_path: str | None = None, test_runner: str | None = None) -> Effective:
    lines = ["targets:", "  s:", "    bundleId: com.x"]
    if app_path is not None:
        lines.append(f"    appPath: {app_path}")
    lines += ["    xcuitest:", "      deviceType: device"]
    if test_runner is not None:
        lines.append(f"      testRunner: {test_runner}")
    return resolve(load_config("\n".join(lines) + "\n"), "s")


# --- the destination string (pure) --- #


def test_destination_targets_the_simulator_by_default() -> None:
    assert _destination("simulator", "ABC123") == "platform=iOS Simulator,id=ABC123"


def test_destination_targets_a_real_device() -> None:
    # A real device drops the "Simulator" suffix; xcodebuild then addresses the attached device.
    assert _destination("device", _DEVICE_UDID) == f"platform=iOS,id={_DEVICE_UDID}"


def test_destination_validates_the_udid() -> None:
    # A leading-dash id would be read by xcodebuild as an option — refuse it (the shared device_id
    # policy `validated_udid` enforces, applied to a real-device id the same as a simulator one).
    with pytest.raises(simctl.DeviceError):
        _destination("device", "-rf")


# --- real-device start(): skip simctl, refuse simctl-only operations loudly --- #


def test_start_on_a_real_device_refuses_simctl_install() -> None:
    # A real device installs its build out of band; asking the simctl installer to place `appPath`
    # must fail loudly (BE-0238 Unit 2/3), never silently skip (determinism first).
    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID)
    with pytest.raises(simctl.DeviceError, match="real device"):
        env.start(_device_eff(app_path="build/App.app"), Preconditions())


def test_start_on_a_real_device_refuses_permission_grants() -> None:
    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID)
    with pytest.raises(simctl.DeviceError, match="real device"):
        env.start(_device_eff(), Preconditions(), permissions={"camera": "yes"})


def test_start_on_a_real_device_refuses_erase() -> None:
    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID)
    with pytest.raises(simctl.DeviceError, match="real device"):
        env.start(_device_eff(), Preconditions(erase=True))


def test_start_on_a_real_device_targets_the_device_and_skips_simctl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # The happy real-device path: no simctl call, and the xcodebuild destination is the device.
    # Faked at the process boundary (Popen) and the driver factory — the runner needs Xcode + a
    # device, which the gate has neither of.
    runner = tmp_path / "Runner.xctestrun"
    with runner.open("wb") as f:
        plistlib.dump({"Target": {"TestingEnvironmentVariables": {}}}, f)

    simctl_calls: list[list[str]] = []

    def _record_run(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        simctl_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    captured: dict[str, list[str]] = {}

    class _FakeProc:
        def terminate(self) -> None: ...
        def wait(self, timeout: float | None = None) -> int:
            return 0

    def _fake_popen(argv: list[str], **_kw: Any) -> _FakeProc:
        captured["argv"] = argv
        return _FakeProc()

    class _FakeDriver:
        def await_ready(self, timeout: float) -> None: ...

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(backends, "make_driver", lambda *_a, **_k: _FakeDriver())

    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID, env_run=_record_run)
    env.start(_device_eff(test_runner=str(runner)), Preconditions())

    assert f"platform=iOS,id={_DEVICE_UDID}" in captured["argv"]
    assert simctl_calls == []  # a real device is never touched through simctl


# --- the live-route boundary: an Appium endpoint is not yet a drivable udid (BE-0238 Unit 4) --- #


def test_appium_endpoint_is_rejected_by_the_udid_machinery() -> None:
    # Unit 4 shipped the `appium` DeviceProvider as a seam only: it hands a run the reserved device's
    # Appium / WebDriver endpoint as the udid spec, but that value flows unchanged into `_destination`,
    # whose `validated_udid` applies the shared device_id policy — and a URL's `//` is outside that
    # charset. So a real `http(s)://` endpoint raises `invalid udid` today: the documented reason the
    # live route is not yet end-to-end runnable, since the follow-on transport must route the endpoint
    # around the simctl / xcodebuild udid machinery, which structurally cannot carry a URL. Pin the
    # boundary here — when that transport lands, this test breaks visibly and is the cue to update it.
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        _destination("device", "http://grid.local:4723")


def test_appium_lease_endpoint_reaches_the_udid_machinery_unchanged() -> None:
    # Tie the seam to the boundary above: the endpoint the `appium` provider yields is exactly the
    # value that reaches `_destination` as the udid — the run resolves its lanes against `udid_spec` —
    # so the live route fails closed the same way today, never a silent fall back to a local device.
    from bajutsu.runner import device_provider as dp

    cfg = load_config(
        "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: device\n"
        "    deviceProvider:\n      kind: appium\n      endpoint: http://grid.local:4723\n"
    )
    lease = dp.acquire_device(resolve(cfg, "s"), "booted")
    assert lease.udid_spec == "http://grid.local:4723"  # the endpoint, not the --udid flag
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        _destination("device", lease.udid_spec)

"""Reading the facts `runsOn` conditions match against, from fake simctl and adb output (BE-0452)."""

from __future__ import annotations

import json
import subprocess

import pytest

from bajutsu.common.devices.facts import (
    DeviceFacts,
    DeviceReadError,
    read_android,
    read_ios,
    read_ios_device,
    read_ios_pool,
    read_web,
)

_DEVICES = {
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-17-5": [
            {
                "udid": "A",
                "name": "my phone",
                "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-SE-3rd-generation",
            }
        ],
        "com.apple.CoreSimulator.SimRuntime.iOS-18-2": [
            {
                "udid": "B",
                "name": "renamed",
                "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPad-Pro-13-inch-M4",
            }
        ],
        "com.apple.CoreSimulator.SimRuntime.watchOS-11-0": [
            {"udid": "W", "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.Watch"}
        ],
    }
}
_TYPES = {
    "devicetypes": [
        {
            "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-SE-3rd-generation",
            "name": "iPhone SE (3rd generation)",
            "productFamily": "iPhone",
        },
        {
            "identifier": "com.apple.CoreSimulator.SimDeviceType.iPad-Pro-13-inch-M4",
            "name": "iPad Pro 13-inch (M4)",
            "productFamily": "iPad",
        },
        {
            "identifier": "com.apple.CoreSimulator.SimDeviceType.Watch",
            "name": "Watch",
            "productFamily": "Apple Watch",
        },
    ]
}


def _simctl(devices: object = _DEVICES, types: object = _TYPES):  # type: ignore[no-untyped-def]
    def run(args: list[str], _env: object = None) -> str:
        return json.dumps(types if "devicetypes" in args else devices)

    return run


def test_ios_reads_the_device_type_name_not_the_device_name() -> None:
    facts = read_ios(["B", "A"], _simctl())
    assert [(f.udid, f.model, f.release) for f in facts] == [
        ("B", "iPad Pro 13-inch (M4)", "18.2"),
        ("A", "iPhone SE (3rd generation)", "17.5"),
    ]
    assert facts[1].describe == 'iOS 17.5 "iPhone SE (3rd generation)" (A)'


def test_ios_reads_the_family_from_the_device_type() -> None:
    assert [f.family for f in read_ios(["A", "B"], _simctl())] == ["iPhone", "iPad"]


def test_ios_reads_a_family_no_condition_names_rather_than_failing() -> None:
    devices = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-2": [
                {"udid": "V", "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.TV"}
            ]
        }
    }
    types = {
        "devicetypes": [
            {
                "identifier": "com.apple.CoreSimulator.SimDeviceType.TV",
                "name": "Apple TV",
                "productFamily": "Apple TV",
            }
        ]
    }
    assert read_ios(["V"], _simctl(devices, types))[0].family == "Apple TV"


def test_ios_fails_on_a_device_type_with_no_family() -> None:
    types = {
        "devicetypes": [
            {
                "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-SE-3rd-generation",
                "name": "iPhone SE (3rd generation)",
            }
        ]
    }
    with pytest.raises(DeviceReadError, match="reports no productFamily"):
        read_ios(["A"], _simctl(types=types))


def test_ios_fails_on_an_unknown_udid_a_foreign_runtime_or_an_unnamed_type() -> None:
    with pytest.raises(DeviceReadError, match="not an available Simulator"):
        read_ios(["Z"], _simctl())
    with pytest.raises(DeviceReadError, match="cannot read an iOS version"):
        read_ios(["W"], _simctl())
    with pytest.raises(DeviceReadError, match="no device type"):
        read_ios(["A"], _simctl(types={"devicetypes": []}))


def test_ios_fails_when_simctl_cannot_be_read() -> None:
    def broken(args: list[str], _env: object = None) -> str:
        raise subprocess.CalledProcessError(1, args)

    with pytest.raises(DeviceReadError, match="could not list"):
        read_ios(["A"], broken)


def _adb(props: dict[str, dict[str, str]], console: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    def run(args: list[str]) -> str:
        serial = args[2]
        if args[3:5] == ["emu", "avd"]:
            if console is None or serial not in console:
                raise subprocess.CalledProcessError(1, args)
            return console[serial]
        return props[serial].get(args[-1], "")

    return run


def test_android_reads_the_api_level_and_the_avd_name_from_either_property() -> None:
    run = _adb(
        {
            "emulator-5554": {"ro.build.version.sdk": "34", "ro.boot.qemu.avd_name": "Pixel_8"},
            "emulator-5556": {
                "ro.build.version.sdk": "30",
                "ro.kernel.qemu.avd_name": "Pixel_4",
            },
            "R5CT": {"ro.build.version.sdk": "33", "ro.product.model": "SM-S911B"},
        }
    )
    facts = read_android(["emulator-5554", "emulator-5556", "R5CT"], run)
    assert facts == [
        DeviceFacts("emulator-5554", "android", "Pixel_8", "34", avd="Pixel_8"),
        DeviceFacts("emulator-5556", "android", "Pixel_4", "30", avd="Pixel_4"),
        DeviceFacts("R5CT", "android", "SM-S911B", "33"),
    ]
    assert facts[0].describe == 'API level 34 "Pixel_8" (emulator-5554)'


def test_android_asks_the_emulator_console_when_no_property_names_the_avd() -> None:
    run = _adb(
        {"10.0.2.2:5555": {"ro.build.version.sdk": "35", "ro.kernel.qemu": "1"}},
        console={"10.0.2.2:5555": "Medium_Phone\nOK\n"},
    )
    assert read_android(["10.0.2.2:5555"], run)[0].avd == "Medium_Phone"


def test_android_fails_on_an_emulator_with_no_avd_name_or_an_odd_api_level() -> None:
    no_name = _adb({"emulator-5554": {"ro.build.version.sdk": "34"}})
    with pytest.raises(DeviceReadError, match="reports no AVD name"):
        read_android(["emulator-5554"], no_name)
    odd = _adb({"emulator-5554": {"ro.build.version.sdk": "UpsideDownCake"}})
    with pytest.raises(DeviceReadError, match="not an API level"):
        read_android(["emulator-5554"], odd)


def test_android_fails_when_a_property_cannot_be_read() -> None:
    def broken(args: list[str]) -> str:
        raise OSError("adb gone")

    with pytest.raises(DeviceReadError, match=r"could not read ro\.build\.version\.sdk"):
        read_android(["emulator-5554"], broken)


def test_web_reads_the_launched_browser_version() -> None:
    facts = read_web("chromium", lambda engine: "130.0.6723.31")
    assert (facts.model, facts.release, facts.version.major) == ("chromium", "130.0.6723.31", 130)
    assert facts.describe == "chromium 130.0.6723.31"
    with pytest.raises(DeviceReadError, match="not one"):
        read_web("webkit", lambda engine: "unknown")


def test_android_fails_naming_the_serial_when_a_read_hangs() -> None:
    def hangs(args: list[str]) -> str:
        raise subprocess.TimeoutExpired(args, 15)

    with pytest.raises(
        DeviceReadError, match=r"emulator-5554: reading ro\.build\.version\.sdk did"
    ):
        read_android(["emulator-5554"], hangs)

    def console_hangs(args: list[str]) -> str:
        if args[3:5] == ["emu", "avd"]:
            raise subprocess.TimeoutExpired(args, 15)
        return "34" if args[-1] == "ro.build.version.sdk" else ""

    with pytest.raises(DeviceReadError, match="emulator-5554: `adb emu avd name` did not answer"):
        read_android(["emulator-5554"], console_hangs)


def test_the_default_android_reader_passes_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from bajutsu.common.devices import facts

    seen: dict[str, object] = {}

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="34\n")

    monkeypatch.setattr(subprocess, "run", run)
    assert facts._timed_adb(["adb", "-s", "x", "shell", "getprop", "p"]) == "34\n"
    assert seen["timeout"] == facts._ADB_READ_TIMEOUT_S


def _device(udid: str, reality: str = "physical", **hardware: str) -> dict[str, object]:
    return {
        "identifier": f"core-{udid}",
        "hardwareProperties": {"udid": udid, "reality": reality, **hardware},
        "deviceProperties": {"osVersionNumber": "18.1.2"},
    }


def _devicectl(*devices: dict[str, object]):  # type: ignore[no-untyped-def]
    def run(args: list[str]) -> str:
        assert args[-2:] == ["--json-output", "-"]
        return json.dumps({"info": {"outcome": "success"}, "result": {"devices": list(devices)}})

    return run


def test_ios_device_reads_the_family_of_each_real_device_in_order() -> None:
    # The patch level drops, as `read_ios` keeps `major.minor`, so releases group alike.
    run = _devicectl(
        _device("P1", deviceType="iPhone", marketingName="iPhone 14 Pro", productType="iPhone15,2"),
        _device("T1", deviceType="iPad", productType="iPad13,8"),
    )
    facts = read_ios_device(["T1", "P1"], run)
    assert facts == [
        DeviceFacts("T1", "ios", "iPad13,8", "18.1", family="iPad", physical=True),
        DeviceFacts("P1", "ios", "iPhone 14 Pro", "18.1", family="iPhone", physical=True),
    ]


def test_ios_device_matches_on_the_hardware_udid_and_skips_simulators() -> None:
    run = _devicectl(_device("S1", reality="simulated", deviceType="iPhone"))
    with pytest.raises(DeviceReadError, match="not a paired real device"):
        read_ios_device(["S1"], run)
    with pytest.raises(DeviceReadError, match="not a paired real device"):
        read_ios_device(["core-S1"], run)


def test_ios_device_fails_on_a_missing_device_type_or_os_version() -> None:
    with pytest.raises(DeviceReadError, match="reports no deviceType"):
        read_ios_device(["P1"], _devicectl(_device("P1")))
    bare = _device("P1", deviceType="iPhone")
    bare["deviceProperties"] = {}
    with pytest.raises(DeviceReadError, match="reports no osVersionNumber"):
        read_ios_device(["P1"], _devicectl(bare))
    bare["deviceProperties"] = {"osVersionNumber": "n/a"}
    with pytest.raises(DeviceReadError, match="'n/a', which is not one"):
        read_ios_device(["P1"], _devicectl(bare))


def test_ios_device_names_a_device_by_its_family_when_nothing_else_names_it() -> None:
    facts = read_ios_device(["P1"], _devicectl(_device("P1", deviceType="iPad")))
    assert facts[0].model == "iPad"


def test_ios_device_fails_on_an_empty_or_malformed_listing() -> None:
    with pytest.raises(DeviceReadError, match="not a paired real device"):
        read_ios_device(["P1"], lambda args: json.dumps({"info": {}}))
    with pytest.raises(DeviceReadError, match="unexpected shape"):
        read_ios_device(["P1"], lambda args: json.dumps({"result": {"devices": ["P1"]}}))
    odd = _device("P1", deviceType="iPhone")
    odd["deviceProperties"] = ["x"]
    with pytest.raises(DeviceReadError, match="unexpected shape"):
        read_ios_device(["P1"], _devicectl(odd))


def test_ios_device_fails_when_devicectl_cannot_be_read_or_hangs() -> None:
    def broken(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args)

    def hangs(args: list[str]) -> str:
        raise subprocess.TimeoutExpired(args, 30)

    with pytest.raises(DeviceReadError, match="could not list the real devices"):
        read_ios_device(["P1"], broken)
    with pytest.raises(DeviceReadError, match="devicectl did not answer"):
        read_ios_device(["P1"], hangs)


def test_the_default_devicectl_reader_passes_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from bajutsu.common.devices import facts

    seen: dict[str, object] = {}

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="{}")

    monkeypatch.setattr(subprocess, "run", run)
    assert facts._timed_devicectl(["xcrun", "devicectl"]) == "{}"
    assert seen["timeout"] == facts._DEVICECTL_READ_TIMEOUT_S


def test_ios_pool_reads_each_udid_through_the_listing_that_shows_it() -> None:
    # BE-0468: a target that lists both kinds reads a pool mixing Simulators and real devices.
    real = _devicectl(_device("P1", deviceType="iPhone", marketingName="iPhone 14 Pro"))
    pool = read_ios_pool(["P1", "B"], _simctl(), real)
    assert [(f.udid, f.family, f.physical) for f in pool] == [
        ("P1", "iPhone", True),
        ("B", "iPad", False),
    ]


def test_ios_pool_fails_on_a_udid_neither_listing_shows() -> None:
    with pytest.raises(DeviceReadError, match="not a paired real device"):
        read_ios_pool(["A", "ghost"], _simctl(), _devicectl())


def test_ios_pool_fails_when_simctl_cannot_be_read() -> None:
    def broken(_args: list[str], _env: object = None) -> str:
        raise OSError("no xcrun")

    with pytest.raises(DeviceReadError, match="could not list the Simulators"):
        read_ios_pool(["A"], broken, _devicectl())

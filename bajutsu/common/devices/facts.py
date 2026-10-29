"""The facts a run matches `runsOn` conditions against, read from each available device (BE-0452).

Every reader here fails loudly rather than guessing: a device whose model or version cannot be read
would otherwise drop out of matching silently, or match a condition it does not meet. The comparison
itself lives in `runs`; this module only reads.

| Platform | Model | Release | Family |
|---|---|---|---|
| iOS Simulator | the simctl device-type name of the udid | the runtime label's version (`18.2`) | the device type's `productFamily` |
| iOS real device | devicectl's marketing name, shown only | devicectl's `osVersionNumber` | devicectl's `deviceType` |
| Android | the AVD name for an emulator, `ro.product.model` for a physical device | `ro.build.version.sdk` | none |
| Web | the engine | Playwright's `browser.version` | none |

A family is kept as the device reports it (`iPhone`, `iPad`, `Apple TV`), so a family a `family`
condition never names is still readable rather than an error (BE-0468).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from bajutsu.common.backend_cli import adb, simctl
from bajutsu.common.devices import os as device_os
from bajutsu.common.devices.errors import DeviceError
from bajutsu.common.devices.version import Version

# The AVD-name properties, newest images first: images from API level 31 expose the emulator's
# kernel-command-line name as `ro.boot.qemu.avd_name`, older ones as `ro.kernel.qemu.avd_name`
# (BE-0452 unit 2). Neither read is verified on a real emulator at every supported API level yet.
_AVD_PROPS = ("ro.boot.qemu.avd_name", "ro.kernel.qemu.avd_name")
# A property that reads "1" on an emulator image and is unset on a physical device.
_EMULATOR_PROPS = ("ro.boot.qemu", "ro.kernel.qemu")
# How long `devicectl list devices` may take; it answers in a second or two on a healthy host.
_DEVICECTL_READ_TIMEOUT_S = 30.0
# How long one adb read may take. A wedged device or emulator console otherwise hangs the run
# before its first step; a property read answers in well under a second on a healthy device.
_ADB_READ_TIMEOUT_S = 15.0


def _timed_adb(args: list[str]) -> str:
    """Run one adb read, raising `subprocess.TimeoutExpired` once it outlasts the read timeout."""
    return subprocess.run(
        args, capture_output=True, text=True, check=True, timeout=_ADB_READ_TIMEOUT_S
    ).stdout


def _timed_out(serial: str, what: str) -> DeviceReadError:
    return DeviceReadError(
        f"{serial}: {what} did not answer within {_ADB_READ_TIMEOUT_S:g}s, so runsOn cannot match it"
    )


class DeviceReadError(DeviceError):
    """A device's model or release could not be read, so no condition can be matched against it."""


@dataclass(frozen=True)
class DeviceFacts:
    """What one available device is, as `runsOn` conditions compare it.

    `model` is what a `model` (iOS) or `avd` (Android) condition compares exactly; `release` is what
    a version range compares. `avd` stays None for a physical Android device, which never matches an
    `avd` condition. `family` is what a `family` condition compares (BE-0468); it stays None on
    Android and the web, which have no such condition.
    """

    udid: str
    platform: str
    model: str
    release: str
    avd: str | None = None
    family: str | None = None

    @property
    def version(self) -> Version:
        """The release as a three-component version (`34` reads as `34.0.0`)."""
        return Version.parse(self.release)

    @property
    def describe(self) -> str:
        """How a message names the device: `iOS 17.5 "iPhone 15" (5A3F...)`."""
        if self.platform == "web":
            return f"{self.model} {self.release}"
        prefix = "iOS" if self.platform == "ios" else "API level"
        return f'{prefix} {self.release} "{self.model}" ({self.udid})'


def read_ios(udids: Sequence[str], run: simctl.RunFn = simctl.real_run) -> list[DeviceFacts]:
    """Read the device-type name and runtime version of each Simulator, in *udids* order.

    Two simctl listings serve every udid: the devices, whose runtime key and `deviceTypeIdentifier`
    say what each udid is, and the device types, which give the identifier its exact name
    (`iPhone SE (3rd generation)`) and its `productFamily` (`iPhone`, `iPad`). A device's own name
    is free-form, so it is never compared.

    Raises:
        DeviceReadError: simctl could not be read, a udid is not an available Simulator, or its
            device type or runtime has no readable name, family, or version.
    """
    try:
        devices = json.loads(run(simctl.list_devices_cmd(), None))
        types = json.loads(run(simctl.list_devicetypes_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise DeviceReadError(f"could not list the Simulators to match runsOn: {exc}") from exc
    names = {
        str(t.get("identifier")): (str(t.get("name")), str(t.get("productFamily") or ""))
        for t in types.get("devicetypes") or []
        if t.get("identifier") and t.get("name")
    }
    found: dict[str, tuple[str, str]] = {}
    for runtime, entries in (devices.get("devices") or {}).items():
        for dev in entries:
            if dev.get("udid"):
                found[str(dev["udid"])] = (str(runtime), str(dev.get("deviceTypeIdentifier") or ""))
    facts: list[DeviceFacts] = []
    for udid in udids:
        if udid not in found:
            raise DeviceReadError(
                f"{udid} is not an available Simulator, so runsOn cannot match it"
            )
        runtime, type_id = found[udid]
        parsed = device_os.parse(simctl.runtime_label(runtime))
        if parsed is None or parsed.platform != "ios":
            raise DeviceReadError(f"{udid}: cannot read an iOS version from runtime {runtime!r}")
        if type_id not in names:
            raise DeviceReadError(f"{udid}: simctl lists no device type named {type_id!r}")
        model, family = names[type_id]
        if not family:
            raise DeviceReadError(f"{udid}: device type {model!r} reports no productFamily")
        facts.append(
            DeviceFacts(
                udid=udid,
                platform="ios",
                model=model,
                release=f"{parsed.major}.{parsed.minor}",
                family=family,
            )
        )
    return facts


def _timed_devicectl(args: list[str]) -> str:
    """Run one devicectl read, raising `subprocess.TimeoutExpired` once it outlasts its timeout."""
    return subprocess.run(
        args, capture_output=True, text=True, check=True, timeout=_DEVICECTL_READ_TIMEOUT_S
    ).stdout


def _physical_devices(listing: Any) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Each real device of a devicectl listing by its udid, with its hardware and device properties.

    Raises:
        AttributeError, TypeError: The listing is not shaped as devicectl writes it.
    """
    found: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for dev in (listing.get("result") or {}).get("devices") or []:
        hardware = dev.get("hardwareProperties") or {}
        properties = dev.get("deviceProperties") or {}
        if not isinstance(properties, dict):
            raise TypeError(f"deviceProperties is {type(properties).__name__}, not an object")
        if hardware.get("reality") == "physical" and hardware.get("udid"):
            found[str(hardware["udid"])] = (hardware, properties)
    return found


def read_ios_device(
    udids: Sequence[str], run: Callable[[list[str]], str] = _timed_devicectl
) -> list[DeviceFacts]:
    """Read the family of each paired real iOS device, in *udids* order (BE-0468).

    One `devicectl list devices` listing serves every udid. The listing shows Simulators too, so
    only an entry whose `reality` is `physical` counts, and it is matched on
    `hardwareProperties.udid`, the id an XCUITest destination takes, not on devicectl's own
    `identifier`. A listed device is not checked for reachability: its `tunnelState` reads
    `disconnected` until a run opens the tunnel, so that field cannot tell an idle device from an
    unplugged one, and driving it is what fails loudly instead.

    The family is the only fact a condition compares on a real device, so `model` and `os` stay
    refused there. `model` here only names the device in messages: it is devicectl's marketing name
    when there is one, which can equal a Simulator device-type name, and `productType`
    (`iPad13,8`) otherwise. The release keeps `major.minor`, as `read_ios` does, so a real device
    and a Simulator on the same release group together whatever their patch level.

    Raises:
        DeviceReadError: devicectl could not be read or did not answer in time, a udid is not a
            paired real device, or it reports no device type or a version that is not one.
    """
    args = ["xcrun", "devicectl", "list", "devices", "--json-output", "-"]
    try:
        listing = json.loads(run(args))
    except subprocess.TimeoutExpired as exc:
        raise DeviceReadError(
            f"devicectl did not answer within {_DEVICECTL_READ_TIMEOUT_S:g}s, "
            "so runsOn cannot match a real device"
        ) from exc
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise DeviceReadError(f"could not list the real devices to match runsOn: {exc}") from exc
    try:
        found = _physical_devices(listing)
    except (AttributeError, TypeError) as exc:
        raise DeviceReadError(
            f"devicectl listed the devices in an unexpected shape: {exc}"
        ) from exc
    facts: list[DeviceFacts] = []
    for udid in udids:
        if udid not in found:
            raise DeviceReadError(f"{udid} is not a paired real device, so runsOn cannot match it")
        hardware, properties = found[udid]
        family = str(hardware.get("deviceType") or "")
        release = str(properties.get("osVersionNumber") or "")
        if not family:
            raise DeviceReadError(f"{udid}: devicectl reports no deviceType")
        if not release:
            raise DeviceReadError(f"{udid}: devicectl reports no osVersionNumber")
        try:
            version = Version.parse(release)
        except ValueError as exc:
            raise DeviceReadError(
                f"{udid}: devicectl reports OS version {release!r}, which is not one"
            ) from exc
        model = str(hardware.get("marketingName") or hardware.get("productType") or family)
        facts.append(
            DeviceFacts(
                udid=udid,
                platform="ios",
                model=model,
                release=f"{version.major}.{version.minor}",
                family=family,
            )
        )
    return facts


def _prop(serial: str, name: str, run: adb.RunFn) -> str:
    try:
        return run(adb.get_prop_cmd(serial, name)).strip()
    except subprocess.TimeoutExpired as exc:
        raise _timed_out(serial, f"reading {name}") from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise DeviceReadError(f"{serial}: could not read {name}: {exc}") from exc


def _avd_name(serial: str, run: adb.RunFn) -> str | None:
    """The emulator's AVD name: either property, then the emulator console, or None."""
    for prop in _AVD_PROPS:
        if value := _prop(serial, prop, run):
            return value
    # A local emulator answers on its console too; its reply ends with an `OK` line.
    try:
        reply = run(adb.emu_avd_name_cmd(serial))
    except subprocess.TimeoutExpired as exc:
        raise _timed_out(serial, "`adb emu avd name`") from exc
    except (subprocess.CalledProcessError, OSError):
        return None
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    name = next((line for line in lines if line != "OK"), "")
    return name or None


def read_android(serials: Sequence[str], run: adb.RunFn = _timed_adb) -> list[DeviceFacts]:
    """Read the API level, and the AVD name or product model, of each device, in *serials* order.

    A physical device is matched like an emulator except that it has no AVD: its model is
    `ro.product.model`, and it never matches an `avd` condition.

    Raises:
        DeviceReadError: A property could not be read or did not answer in time, the API level is
            not a number, or an emulator reports no AVD name, which would leave every `avd`
            condition missing it without a word.
    """
    facts: list[DeviceFacts] = []
    for serial in serials:
        sdk = _prop(serial, "ro.build.version.sdk", run)
        if not sdk.isdigit():
            raise DeviceReadError(f"{serial}: ro.build.version.sdk reads {sdk!r}, not an API level")
        emulator = serial.startswith("emulator-") or any(
            _prop(serial, prop, run) == "1" for prop in _EMULATOR_PROPS
        )
        if emulator:
            avd = _avd_name(serial, run)
            if avd is None:
                raise DeviceReadError(
                    f"{serial} is an emulator but reports no AVD name "
                    f"({', '.join(_AVD_PROPS)}, or `adb emu avd name`)"
                )
            facts.append(
                DeviceFacts(udid=serial, platform="android", model=avd, release=sdk, avd=avd)
            )
        else:
            model = _prop(serial, "ro.product.model", run)
            facts.append(DeviceFacts(udid=serial, platform="android", model=model, release=sdk))
    return facts


def playwright_version(engine: str) -> str:
    """The version of the browser Playwright launches for *engine*, read from a brief headless launch.

    Raises:
        DeviceReadError: The browser could not be launched to read its version.
    """
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = getattr(pw, engine).launch(headless=True)
            try:
                return str(browser.version)
            finally:
                browser.close()
    except Exception as exc:
        raise DeviceReadError(f"could not launch {engine} to read its version: {exc}") from exc


def read_web(engine: str, version_of: Callable[[str], str] = playwright_version) -> DeviceFacts:
    """The facts of the browser a web lane launches for *engine*.

    Raises:
        DeviceReadError: The version could not be read.
    """
    release = version_of(engine)
    try:
        Version.parse(release)
    except ValueError as exc:
        raise DeviceReadError(f"{engine} reports version {release!r}, which is not one") from exc
    return DeviceFacts(udid=f"web-{engine}", platform="web", model=engine, release=release)

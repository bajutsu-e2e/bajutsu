"""`runsOn.android.kind`, `kind: any`, and the split Android routes (BE-0518 units 1 and 2).

Every check runs on the config, recorded property listings, and a scripted `adb`: no device.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from pydantic import ValidationError

from bajutsu.common.backend_cli import adb
from bajutsu.common.capability.route_tokens import route_tokens, worker_routes
from bajutsu.common.capability.settings_support import route_for, route_settings
from bajutsu.common.config import AndroidConfig, Effective, load_config, resolve
from bajutsu.common.config.preconditions import ResolvedPreconditions
from bajutsu.common.devices.facts import DeviceFacts, read_android
from bajutsu.common.devices.runs import first_run, form_target_runs
from bajutsu.common.platform_lifecycle.environments.android import AndroidEnvironment
from bajutsu.common.platform_lifecycle.protocols import ProvisionProfile
from bajutsu.common.scenario.models.hook_command import HookWhen, JudgedTarget
from bajutsu.run.device_matrix import apply_kind, declared_kinds, driven_kind


def _target(platform: str, runs_on: str = "", driver: str = "") -> str:
    parts = [f"platform: {platform}", "subject: { id: com.x }"]
    parts += [f"runsOn: {runs_on}"] if runs_on else []
    parts += [f"driver: {driver}"] if driver else []
    return "targets:\n  t: { " + ", ".join(parts) + " }\n"


def _eff(platform: str, runs_on: str = "", driver: str = "") -> Effective:
    return resolve(load_config(_target(platform, runs_on, driver)), "t")


def _android(kind: str | None = None) -> Effective:
    return _eff("android", f"{{android: {{kind: {kind}}}}}" if kind else "")


# --- config -----------------------------------------------------------------------------------


def test_an_android_target_runs_on_an_emulator_by_default() -> None:
    eff = _android()
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.device_kind == "emulator"
    # A single kind is how the target is driven, not a condition that multiplies runs.
    assert "kind" not in eff.conditions
    assert driven_kind(eff) == "emulator"


def test_a_device_target_drives_a_physical_device() -> None:
    eff = _android("device")
    assert driven_kind(eff) == "device"
    assert declared_kinds(eff) == ["device"]


def test_a_listed_android_kind_is_a_condition_in_written_order() -> None:
    eff = _android("[device, emulator]")
    assert eff.conditions["kind"] == ["device", "emulator"]
    assert declared_kinds(eff) == ["device", "emulator"]
    assert driven_kind(eff) is None


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_any_is_a_condition_over_both_kinds_virtual_first(platform: str) -> None:
    eff = _eff(platform, f"{{{platform}: {{kind: any}}}}")
    assert eff.conditions["kind"] == "any"
    virtual = "simulator" if platform == "ios" else "emulator"
    assert declared_kinds(eff) == [virtual, "device"]


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_a_list_may_not_hold_any(platform: str) -> None:
    virtual = "simulator" if platform == "ios" else "emulator"
    with pytest.raises(ValidationError, match=re.escape(f"runsOn.{platform}.kind lists 'any'")):
        load_config(_target(platform, f"{{{platform}: {{kind: [{virtual}, any]}}}}"))


def test_an_unknown_android_kind_names_the_allowed_values() -> None:
    with pytest.raises(ValidationError, match="use one of emulator, device, any"):
        load_config(_target("android", "{android: {kind: simulator}}"))


def test_an_android_kind_list_may_not_repeat() -> None:
    with pytest.raises(ValidationError, match=r"runsOn\.android\.kind lists a value twice"):
        load_config(_target("android", "{android: {kind: [device, device]}}"))


@pytest.mark.parametrize("kind", ["device", "any", "[emulator, device]"])
def test_a_target_that_may_run_on_a_device_cannot_name_an_avd(kind: str) -> None:
    with pytest.raises(ValidationError, match=r"runsOn\.android\.avd is an emulator condition"):
        load_config(_target("android", f"{{android: {{kind: {kind}, avd: Pixel}}}}"))


def test_a_device_target_keeps_its_api_level() -> None:
    eff = _eff("android", '{android: {kind: device, apiLevel: "34"}}')
    assert eff.conditions == {"apiLevel": "34"}


@pytest.mark.parametrize("field", ["model: 'iPhone 15'", "os: '17'"])
def test_an_ios_any_target_cannot_name_a_device_condition(field: str) -> None:
    with pytest.raises(ValidationError, match="kind is any or includes device cannot declare"):
        load_config(_target("ios", f"{{ios: {{kind: any, {field}}}}}"))


def test_an_ios_any_target_cannot_name_a_prebuilt_runner() -> None:
    with pytest.raises(ValidationError, match=r"runsOn\.ios\.kind is any cannot declare"):
        load_config(_target("ios", "{ios: {kind: any}}", "{runner: {testRunner: r.app}}"))


def test_an_ios_kind_list_keeps_its_prebuilt_runner() -> None:
    # A list names every kind it runs, so one runner can be built for all of them.
    load_config(
        _target("ios", "{ios: {kind: [simulator, device]}}", "{runner: {testRunner: r.app}}")
    )


def test_a_run_applies_its_kind_to_an_android_target() -> None:
    eff = apply_kind(_android("[emulator, device]"), "device")
    assert "kind" not in eff.conditions
    assert driven_kind(eff) == "device"


# --- runs -------------------------------------------------------------------------------------


_EMULATOR = DeviceFacts("emulator-5554", "android", "Pixel", "34", avd="Pixel")
_PHONE = DeviceFacts("R5CT", "android", "SM-S911B", "34", physical=True)


def test_a_single_kind_keeps_the_runs_on_its_own_devices() -> None:
    [run] = form_target_runs("t", "android", {"apiLevel": "34"}, [_PHONE, _EMULATOR], kind="device")
    assert run.outcome == "run"
    assert run.kind == "device"


def test_a_listed_kind_fails_without_a_device_of_that_kind() -> None:
    runs = form_target_runs("t", "android", {"kind": ["emulator", "device"]}, [_EMULATOR])
    assert [(r.kind, r.outcome) for r in runs] == [("emulator", "run"), ("device", "failed")]
    assert runs[1].coordinates == (("kind", "device"),)
    assert 'runsOn.android.kind  "device"' in runs[1].reason


def test_any_stops_the_run_until_the_kind_selection_lands() -> None:
    # TODO(BE-0518): unit 3 replaces this with the choice among the candidate kinds.
    [run] = form_target_runs("t", "android", {"kind": "any"}, [_EMULATOR, _PHONE])
    assert run.outcome == "failed"
    assert "pass --kind to run, record" in run.reason
    with pytest.raises(LookupError, match="pass --kind"):
        first_run("t", "android", {"kind": "any"}, [_EMULATOR, _PHONE])


def test_the_facts_mark_a_physical_android_device() -> None:
    props = {
        ("emulator-5554", "ro.build.version.sdk"): "34",
        ("emulator-5554", "ro.boot.qemu.avd_name"): "Pixel",
        ("R5CT", "ro.build.version.sdk"): "34",
        ("R5CT", "ro.product.model"): "SM-S911B",
    }

    def run(args: list[str]) -> str:
        return props.get((args[2], args[-1]), "")

    assert [(f.udid, f.physical) for f in read_android(["emulator-5554", "R5CT"], run)] == [
        ("emulator-5554", False),
        ("R5CT", True),
    ]


# --- the device a run takes -------------------------------------------------------------------


def _scripted(emulator_image: bool) -> adb.RunFn:
    def run(args: list[str]) -> str:
        if "getprop" in args and args[-1] in adb.EMULATOR_PROPS:
            return "1\n" if emulator_image else "\n"
        if "resolve-activity" in args:
            return f"{args[-1]}/.Main\n"
        return "1\n" if "sys.boot_completed" in args else ""

    return run


@pytest.mark.parametrize(
    ("serial", "emulator_image", "kind", "found"),
    [
        ("R5CT", False, "emulator", "a physical device"),
        ("emulator-5554", False, "device", "an emulator"),
        # An emulator reached with `adb connect` keeps no `emulator-` serial but says what it is.
        ("10.0.0.2:5555", True, "device", "an emulator"),
    ],
)
def test_a_device_of_another_kind_stops_the_run_before_any_device_work(
    serial: str, emulator_image: bool, kind: str, found: str
) -> None:
    calls: list[list[str]] = []
    script = _scripted(emulator_image)

    def run(args: list[str]) -> str:
        calls.append(args)
        return script(args)

    env = AndroidEnvironment("adb", serial, adb_run=run)
    eff = _android(kind)
    with pytest.raises(adb.DeviceError, match=f"{serial} is {found}.*runsOn.android.kind {kind}"):
        env.start(eff, ResolvedPreconditions())
    # Past the boot wait, only the kind was read: nothing was installed, cleared, or launched.
    assert all("getprop" in c for c in calls)


def test_a_missing_adb_reads_as_a_device_error() -> None:
    def no_adb(args: list[str]) -> str:
        raise FileNotFoundError("adb")

    # A provider that hands over a booted device skips the boot wait, so the kind read meets adb first.
    env = AndroidEnvironment(
        "adb", "R5CT", adb_run=no_adb, provision=ProvisionProfile(boot_ready=True)
    )
    with pytest.raises(adb.DeviceError, match="adb not found"):
        env.start(_android("device"), ResolvedPreconditions())


# --- routes -----------------------------------------------------------------------------------


def test_the_android_route_follows_the_kind() -> None:
    assert route_for("adb", _android()) == "android-emulator"
    assert route_for("adb", _android("device")) == "android-device"
    assert route_for("adb", apply_kind(_android("[emulator, device]"), "device")) == (
        "android-device"
    )


def test_the_two_android_routes_start_alike() -> None:
    assert route_settings("android-emulator") == route_settings("android-device")
    emulator = {t.split(":", 2)[2] for t in route_tokens("android-emulator")}
    device = {t.split(":", 2)[2] for t in route_tokens("android-device")}
    assert emulator == device
    assert all(t.split(":")[1] == "android-device" for t in route_tokens("android-device"))


def test_an_adb_worker_drives_both_android_routes() -> None:
    assert worker_routes(["adb"]) == ["android-emulator", "android-device"]


@pytest.mark.parametrize(
    ("routes", "route", "holds"),
    [
        (["android"], "android-emulator", True),
        (["android"], "android-device", True),
        (["android-device"], "android-emulator", False),
        (["android-device"], "android-device", True),
    ],
)
def test_a_hook_route_condition_keeps_android_for_both_routes(
    routes: list[str], route: str, holds: bool
) -> None:
    when = HookWhen.model_validate({"routes": routes})
    assert when.target_holds(JudgedTarget("t", "android", route)) is holds


def test_a_targets_own_route_condition_reaches_an_android_route_through_the_alias() -> None:
    hooks = "{ beforeScenario: [{ cmd: x, when: { routes: [android] } }] }"
    load_config(
        "targets:\n  t: { platform: android, subject: { id: com.x }, hooks: " + hooks + " }\n"
    )


def test_an_unreadable_kind_reads_as_a_device_error() -> None:
    def refused(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args, output="", stderr="error: device unauthorized")

    # Past a provider's boot wait, so the refusal reaches the kind read rather than the boot poll.
    env = AndroidEnvironment(
        "adb", "R5CT", adb_run=refused, provision=ProvisionProfile(boot_ready=True)
    )
    with pytest.raises(adb.DeviceError, match="unauthorized"):
        env.start(_android("device"), ResolvedPreconditions())


def test_a_group_member_of_another_kind_stops_and_the_kind_is_read_once() -> None:
    calls: list[list[str]] = []
    script = _scripted(emulator_image=False)

    def run(args: list[str]) -> str:
        calls.append(args)
        return script(args)

    env = AndroidEnvironment("adb", "R5CT", adb_run=run)
    env.start(_android("device"), ResolvedPreconditions())
    reads = sum(1 for c in calls if "getprop" in c and c[-1] in adb.EMULATOR_PROPS)
    with pytest.raises(adb.DeviceError, match=r"runsOn\.android\.kind emulator"):
        env.start_member(_android(), ResolvedPreconditions())
    # Every member shares the device, so its kind is read on the first start alone.
    assert sum(1 for c in calls if "getprop" in c and c[-1] in adb.EMULATOR_PROPS) == reads


@pytest.mark.parametrize("kind", ["emulator", "device"])
def test_an_unsupported_setting_names_the_android_field_on_either_route(kind: str) -> None:
    from bajutsu.common.capability.settings_support import SettingsCheck

    check = SettingsCheck(
        target="t", route="android-device" if kind == "device" else "android-emulator"
    )
    assert check._field("seedPhotos") == "runsOn.android.seedPhotos"


def test_a_single_command_keeps_to_the_targets_own_kind() -> None:
    # `record`, `crawl`, and `repl` take one device: a phone listed first must not win for an
    # emulator target when a fitting emulator is free.
    run, device = first_run(
        "t", "android", {"apiLevel": "34"}, [_PHONE, _EMULATOR], kind="emulator"
    )
    assert device == _EMULATOR
    assert run.kind == "emulator"

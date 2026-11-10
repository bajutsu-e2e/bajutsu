"""`seedPhotos` on Android: seed a directory Bajutsu owns in shared storage before launch.

Android has no device-wide wipe, so the seed resets one directory instead of requiring
`subject.reinstall: erase`: the run sees exactly the images it lists, in order, and no other image on
the device is touched. Each image gets a fixed, ascending modification time so a date-sorted picker
shows a reproducible order, and a media scan of the directory publishes the result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.capability.settings_support import settings_support
from bajutsu.common.config import (
    AndroidConfig,
    Effective,
    ResolvedPreconditions,
    check_preconditions,
    command_preconditions,
    load_config,
    resolve,
    resolve_preconditions,
)
from bajutsu.common.platform_lifecycle import AndroidEnvironment
from bajutsu.common.scenario import Preconditions, Redact, load_scenarios

_DIR = adb.SEED_PHOTOS_DIR

# --- the command builders ---


def test_seed_photo_name_keeps_the_ordinal_and_extension_only() -> None:
    assert adb.seed_photo_name(0, "/fixtures/My Cat.JPG") == "000.jpg"
    assert adb.seed_photo_name(12, "photos/red.png") == "012.png"


@pytest.mark.parametrize("path", ["/fixtures/noext", "/fixtures/x.p;ng", "/fixtures/x.$(id)"])
def test_seed_photo_name_refuses_a_path_without_a_plain_extension(path: str) -> None:
    with pytest.raises(adb.DeviceError, match="needs an image file extension"):
        adb.seed_photo_name(0, path)


def test_seed_command_builders() -> None:
    assert adb.seed_photos_reset_cmd("S") == [
        "adb", "-s", "S", "shell", f"mkdir -p {_DIR} && rm -rf {_DIR}/*",
    ]  # fmt: skip
    assert adb.seed_photos_clear_cmd("S") == [
        "adb", "-s", "S", "shell",
        f"[ -d {_DIR} ] && rm -rf {_DIR}/* && echo {adb.SEED_PHOTOS_CLEARED} || true",
    ]  # fmt: skip
    assert adb.push_cmd("S", "/fixtures/red.png", f"{_DIR}/000.png") == [
        "adb", "-s", "S", "push", "/fixtures/red.png", f"{_DIR}/000.png",
    ]  # fmt: skip
    assert adb.seed_photo_time_cmd("S", 61, "061.png") == [
        "adb", "-s", "S", "shell", "touch", "-t", "202001010101.00", f"{_DIR}/061.png",
    ]  # fmt: skip
    scan = adb.media_scan_cmd("S")
    assert scan[:5] == ["adb", "-s", "S", "shell", "am"]
    assert "--receiver-include-background" in scan
    assert scan[-2:] == ["-d", f"file://{_DIR}"]


def test_seed_command_builders_reject_an_unvalidated_serial() -> None:
    with pytest.raises(adb.DeviceError, match="invalid device serial"):
        adb.seed_photos_reset_cmd("-x")


# --- Env.add_media ---


def test_env_add_media_resets_pushes_stamps_and_scans_in_order() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return ""

    adb.Env("S", run=run).add_media(["/fixtures/red.png", "/fixtures/green.jpg"])
    assert calls == [
        adb.seed_photos_reset_cmd("S"),
        adb.push_cmd("S", "/fixtures/red.png", f"{_DIR}/000.png"),
        adb.seed_photo_time_cmd("S", 0, "000.png"),
        adb.push_cmd("S", "/fixtures/green.jpg", f"{_DIR}/001.jpg"),
        adb.seed_photo_time_cmd("S", 1, "001.jpg"),
        adb.media_scan_cmd("S"),
    ]


def test_env_add_media_checks_every_name_before_touching_the_device() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return ""

    with pytest.raises(adb.DeviceError):
        adb.Env("S", run=run).add_media(["/fixtures/red.png", "/fixtures/noext"])
    assert calls == []


# --- AndroidEnvironment.start ---


def _eff() -> Effective:
    return Effective(
        target="app",
        platform_config=AndroidConfig(package="com.example.app"),
        backend=["android"],
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        capture=[],
        redact=Redact(),
    )


def _start(pre: ResolvedPreconditions) -> list[str]:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        if "sys.boot_completed" in argv:
            return "1\n"
        if "resolve-activity" in argv:
            return "com.example.app/.MainActivity\n"
        return ""

    AndroidEnvironment("adb", "S", adb_run=run).start(_eff(), pre)
    return [" ".join(c) for c in calls]


@pytest.mark.parametrize("reinstall", ["overwrite", "clean", "erase"])
def test_start_seeds_after_the_reset_and_before_launch(reinstall: str) -> None:
    joined = _start(ResolvedPreconditions(reinstall=reinstall, seed_photos=["/f/red.png"]))  # type: ignore[arg-type]
    push_at = next(i for i, j in enumerate(joined) if " push /f/red.png" in j)
    scan_at = next(i for i, j in enumerate(joined) if "MEDIA_SCANNER_SCAN_FILE" in j)
    launch_at = next(i for i, j in enumerate(joined) if "am start -W -n" in j)
    assert push_at < scan_at < launch_at
    if reinstall != "overwrite":
        assert next(i for i, j in enumerate(joined) if "pm clear" in j) < push_at


def test_start_without_seed_photos_only_clears_an_earlier_seed() -> None:
    # A scenario that seeds nothing must not see an earlier scenario's images, yet a device never
    # seeded keeps its shared storage untouched: no directory created, no scan.
    joined = _start(ResolvedPreconditions())
    touching = [j for j in joined if _DIR in j]
    assert touching == [" ".join(adb.seed_photos_clear_cmd("S"))]


@pytest.mark.parametrize(("answer", "scans"), [(f"{adb.SEED_PHOTOS_CLEARED}\n", True), ("", False)])
def test_env_add_media_of_an_empty_list_rescans_only_after_clearing(
    answer: str, scans: bool
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return answer if argv == adb.seed_photos_clear_cmd("S") else ""

    adb.Env("S", run=run).add_media([])
    expected = [adb.seed_photos_clear_cmd("S")] + ([adb.media_scan_cmd("S")] if scans else [])
    assert calls == expected


# --- config, scenario, and the preconditions check ---

_CONFIG = (
    "targets:\n"
    "  app:\n"
    "    platform: android\n"
    "    subject: { id: com.example.app }\n"
    "    runsOn: { android: { avd: Pixel_8, seedPhotos: [/fixtures/green.png] } }\n"
)


def _pre(text: str) -> Preconditions:
    return load_scenarios(f"- name: t\n  preconditions: {text}\n  steps: []\n")[0].preconditions


def test_the_target_list_resolves_and_is_no_device_condition() -> None:
    eff = resolve(load_config(_CONFIG), "app")
    assert eff.seed_photos == ["/fixtures/green.png"]
    assert eff.conditions == {"avd": "Pixel_8"}


def test_the_target_list_rebases_against_the_config_file(tmp_path: Path) -> None:
    eff = resolve(load_config(_CONFIG.replace("/fixtures/", "photos/")), "app")
    assert eff.rebased(tmp_path).seed_photos == [str(tmp_path / "photos/green.png")]


def test_a_scenario_list_replaces_the_target_list() -> None:
    eff = resolve(load_config(_CONFIG), "app")
    assert resolve_preconditions(eff, _pre("{}")).seed_photos == ["/fixtures/green.png"]
    own = _pre("{ device: { android: { seedPhotos: [/fixtures/red.png] } } }")
    assert resolve_preconditions(eff, own).seed_photos == ["/fixtures/red.png"]
    none = _pre("{ device: { android: { seedPhotos: [] } } }")
    assert resolve_preconditions(eff, none).seed_photos == []


def test_the_android_block_does_not_reach_an_ios_target_or_the_reverse() -> None:
    pre = _pre("{ device: { android: { seedPhotos: [a.png] }, ios: { seedPhotos: [b.png] } } }")
    assert pre.seed_photos_for("android") == ["a.png"]
    assert pre.seed_photos_for("ios") == ["b.png"]


def test_the_scenario_list_resolves_against_its_file() -> None:
    pre = _pre("{ device: { android: { seedPhotos: [a.png] } } }")
    pre.resolve_seed_photos(lambda ref: f"/root/{ref}")
    assert pre.seed_photos_for("android") == ["/root/a.png"]


def test_android_seed_photos_need_no_erase() -> None:
    eff = resolve(load_config(_CONFIG), "app")
    assert check_preconditions(_pre("{}"), [eff]) == []
    assert check_preconditions(_pre("{}"), [eff], erase_flag=False) == []
    assert command_preconditions(eff, erase=False).seed_photos == ["/fixtures/green.png"]


def test_android_seed_photos_on_a_non_primary_target_are_refused() -> None:
    eff = resolve(load_config(_CONFIG), "app")
    primary = resolve(
        load_config(_CONFIG.replace(", seedPhotos: [/fixtures/green.png]", "")), "app"
    )
    problems = check_preconditions(_pre("{}"), [primary, eff])
    assert problems and "applies to the primary target alone" in problems[0]


def test_the_android_route_supports_seed_photos() -> None:
    assert (
        settings_support("adb", resolve(load_config(_CONFIG), "app"))["seedPhotos"] == "supported"
    )

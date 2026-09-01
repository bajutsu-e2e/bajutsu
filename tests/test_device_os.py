"""The parsed device OS (BE-0358): the label table, the unknown cases, the run-level reduction, and
the one channel that hands the parsed value to a driver."""

from __future__ import annotations

import pytest

from bajutsu import backends, device_os
from bajutsu.common.drivers.xcuitest import XcuitestDriver
from bajutsu.device_os import DeviceOS


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("iOS 18.6", ("ios", 18, 6)),
        ("iOS 26.5", ("ios", 26, 5)),
        ("iOS 26", ("ios", 26, 0)),  # no minor component recorded -> .0, not a guess
        ("iOS 18.6.1", ("ios", 18, 6)),  # patch dropped: same OS as 18.6
        ("Android 14", ("android", 14, 0)),
        ("android 15.1", ("android", 15, 1)),  # the adb label's case is not load-bearing
        ("  iOS 17.2  ", ("ios", 17, 2)),
    ],
)
def test_parse_recognized_labels(label: str, expected: tuple[str, int, int]) -> None:
    parsed = device_os.parse(label)
    assert parsed is not None
    assert (parsed.platform, parsed.major, parsed.minor) == expected
    assert parsed.label == label  # the raw label travels with the parsed fact


@pytest.mark.parametrize(
    "label",
    [
        "",
        "iOS",
        "iOS beta",
        "iOS 18 (beta)",  # a prefix that parses is still not a recognized label
        "watchOS 11.0",  # not a platform Bajutsu runs against
        "18.6",
        None,
        17.2,
    ],
)
def test_parse_returns_none_for_absent_or_unrecognized(label: object) -> None:
    """An unrecognized or absent label is an unknown OS, never a default version."""
    assert device_os.parse(label) is None


def test_patch_release_is_the_same_os_for_grouping() -> None:
    """Equality (and the hash) ignore the raw label, so a history can't split across patch releases."""
    assert device_os.parse("iOS 18.6") == device_os.parse("iOS 18.6.1")
    assert len({device_os.parse("iOS 18.6"), device_os.parse("iOS 18.6.1")}) == 1
    assert device_os.parse("iOS 18.6") != device_os.parse("iOS 18.7")
    assert device_os.parse("iOS 18.6") != device_os.parse("Android 18.6")


def test_display_is_canonical_not_the_raw_label() -> None:
    parsed = device_os.parse("iOS 18.6.1")
    assert parsed is not None and parsed.display == "iOS 18.6"
    android = device_os.parse("Android 14")
    assert android is not None and android.display == "Android 14.0"


def test_describe_spells_out_the_unknown_case() -> None:
    assert device_os.describe(device_os.parse("iOS 18.6")) == "iOS 18.6"
    assert device_os.describe(None) == "unknown OS"


def test_ordering_key_sorts_unknown_last() -> None:
    known = [
        device_os.parse("iOS 26.5"),
        device_os.parse("Android 14"),
        None,
        device_os.parse("iOS 18.6"),
    ]
    assert [device_os.describe(o) for o in sorted(known, key=device_os.ordering_key)] == [
        "Android 14.0",
        "iOS 18.6",
        "iOS 26.5",
        "unknown OS",
    ]


def _manifest(*runtimes: str) -> dict[str, object]:
    return {
        "scenarios": [{"scenario": f"s{i}", "device_runtime": r} for i, r in enumerate(runtimes)]
    }


def test_from_manifest_reads_the_one_os_its_scenarios_agree_on() -> None:
    assert device_os.from_manifest(_manifest("iOS 18.6", "iOS 18.6.1")) == DeviceOS(
        "ios", 18, 6, ""
    )


def test_from_manifest_is_unknown_when_scenarios_span_versions() -> None:
    """A run whose scenarios ran on two OS versions can speak for neither."""
    assert device_os.from_manifest(_manifest("iOS 18.6", "iOS 26.5")) is None
    assert device_os.from_manifest(_manifest("iOS 18.6", "")) is None


def test_from_manifest_is_unknown_without_labels() -> None:
    assert device_os.from_manifest(_manifest()) is None
    assert device_os.from_manifest(_manifest("", "")) is None
    assert device_os.from_manifest({}) is None


# --- the channel to the driver (a `make_driver` keyword, not a `Driver` member) ---


def test_make_driver_hands_the_parsed_os_to_the_xcuitest_driver() -> None:
    parsed = device_os.parse("iOS 18.6")
    driver = backends.make_driver("xcuitest", "UDID", runner_port=1, device_os=parsed)
    assert isinstance(driver, XcuitestDriver) and driver.device_os is parsed


def test_a_driver_built_without_an_os_reports_none() -> None:
    """Absence is a normal state (the web backend and the grid return no device catalog at all)."""
    driver = backends.make_driver("xcuitest", "UDID", runner_port=1)
    assert isinstance(driver, XcuitestDriver) and driver.device_os is None


def test_other_backends_ignore_the_keyword() -> None:
    """It travels as a keyword precisely so no other backend — and no test double — has to declare it."""
    driver = backends.make_driver("fake", "UDID", device_os=device_os.parse("iOS 18.6"))
    assert not hasattr(driver, "device_os")

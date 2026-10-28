"""Forming runs from lists and ranges, combining them, and assigning eligible sets (BE-0452)."""

from __future__ import annotations

import pytest

from bajutsu.common.devices.facts import DeviceFacts
from bajutsu.common.devices.runs import (
    Participant,
    combine,
    eligible_set,
    first_run,
    form_target_runs,
    unpack_conditions,
)

SE = "iPhone SE (3rd generation)"
IPAD = "iPad Pro 13-inch (M4)"


def _ios(udid: str, model: str, release: str) -> DeviceFacts:
    return DeviceFacts(udid, "ios", model, release)


def _emu(serial: str, avd: str, sdk: str) -> DeviceFacts:
    return DeviceFacts(serial, "android", avd, sdk, avd=avd)


POOL = [
    _ios("se17", SE, "17.5"),
    _ios("se18", SE, "18.2"),
    _ios("ipad17", IPAD, "17.5"),
    _ios("ipad18", IPAD, "18.2"),
]


def _labels(runs: list) -> list[tuple[str, str]]:  # type: ignore[type-arg]
    return [(r.label, r.outcome) for r in runs]


def test_a_model_list_and_a_bounded_range_make_one_run_per_value_and_major() -> None:
    runs = form_target_runs("app", "ios", {"model": [SE, IPAD], "os": ">=17 <19"}, POOL)
    assert _labels(runs) == [
        (f"{SE} / iOS 17", "run"),
        (f"{SE} / iOS 18", "run"),
        (f"{IPAD} / iOS 17", "run"),
        (f"{IPAD} / iOS 18", "run"),
    ]


def test_no_condition_makes_one_unconditioned_run() -> None:
    (run,) = form_target_runs("app", "ios", {}, POOL)
    assert (run.label, run.conditioned) == ("", False)
    assert eligible_set(run, POOL) == POOL


def test_a_listed_model_no_device_has_fails_naming_the_value() -> None:
    runs = form_target_runs("app", "ios", {"model": [SE, "iPhone 99"]}, POOL)
    assert _labels(runs) == [(SE, "run"), ("iPhone 99", "failed")]
    assert 'runsOn.ios.model  "iPhone 99"' in runs[1].reason


def test_a_single_model_does_not_multiply_and_fails_without_a_device() -> None:
    (run,) = form_target_runs("app", "ios", {"model": "iPhone 99", "os": ">=17"}, POOL)
    assert (run.label, run.outcome) == ("", "failed")


def test_an_open_range_runs_the_majors_the_pool_has() -> None:
    runs = form_target_runs("app", "ios", {"os": ">=17"}, POOL)
    assert _labels(runs) == [("iOS 17", "run"), ("iOS 18", "run")]


def test_an_open_range_with_no_device_in_it_is_one_not_applicable_record() -> None:
    runs = form_target_runs("app", "ios", {"os": ">=19"}, POOL)
    assert _labels(runs) == [("", "not-applicable")]
    assert runs[0].reason.startswith('runsOn.ios.os  ">=19"  available: iOS 17.5')


def test_an_open_range_beside_a_model_list_counts_per_model() -> None:
    pool = [_ios("se17", SE, "17.5"), _ios("ipad18", IPAD, "18.2")]
    runs = form_target_runs("app", "ios", {"model": [SE, IPAD], "os": ">=18"}, pool)
    assert _labels(runs) == [(SE, "not-applicable"), (f"{IPAD} / iOS 18", "run")]


def test_a_bounded_range_names_majors_the_pool_lacks_and_they_fail_at_assignment() -> None:
    runs = form_target_runs("app", "ios", {"os": "16 - 17"}, POOL)
    assert _labels(runs) == [("iOS 16", "run"), ("iOS 17", "run")]
    combos = combine([Participant(("app",), "ios", (runs[0],))], {"ios": POOL})
    assert combos[0].outcome == "failed"
    assert "iOS 16" in combos[0].reason


def test_a_union_combines_sides_and_a_bounded_side_keeps_the_must_run_outcome() -> None:
    runs = form_target_runs("app", "ios", {"os": ">=17 <19 || >=18"}, POOL)
    assert _labels(runs) == [("iOS 17", "run"), ("iOS 18", "run")]
    # The open side has no device: one not-applicable record beside the bounded side's run.
    runs = form_target_runs("app", "ios", {"os": "17 || >=19"}, POOL)
    assert _labels(runs) == [("iOS 17", "run"), ("", "not-applicable")]


def test_android_api_levels_are_majors_and_a_physical_device_never_matches_an_avd() -> None:
    pool = [
        _emu("emulator-5554", "Pixel_8", "34"),
        _emu("emulator-5556", "Pixel_8", "35"),
        DeviceFacts("R5CT", "android", "Pixel_8", "35"),
    ]
    runs = form_target_runs("droid", "android", {"avd": "Pixel_8", "apiLevel": "33 - 35"}, pool)
    assert _labels(runs) == [("API 33", "run"), ("API 34", "run"), ("API 35", "run")]
    assert eligible_set(runs[0], pool) is None
    assert [d.udid for d in eligible_set(runs[2], pool) or []] == ["emulator-5556"]


def test_the_eligible_set_takes_the_first_model_then_the_newest_release() -> None:
    pool = [
        _ios("a", IPAD, "17.4"),
        _ios("b", SE, "17.5"),
        _ios("c", IPAD, "17.5"),
        _ios("d", IPAD, "17.5"),
    ]
    (run,) = form_target_runs("app", "ios", {"os": "17"}, pool)
    assert [d.udid for d in eligible_set(run, pool) or []] == ["c", "d"]
    # Two devices at once: only the iPad model has two, and only 17.5 has two of them.
    assert [d.udid for d in eligible_set(run, pool, demand=2) or []] == ["c", "d"]
    assert eligible_set(run, pool, demand=3) is None


def test_a_web_engine_list_multiplies_and_a_version_range_narrows() -> None:
    browsers = [
        DeviceFacts("web-chromium", "web", "chromium", "130.0.6723.31"),
        DeviceFacts("web-webkit", "web", "webkit", "18.2"),
    ]
    runs = form_target_runs(
        "site", "web", {"browser.engine": ["chromium", "webkit"]}, [], engine="chromium"
    )
    assert _labels(runs) == [("chromium", "run"), ("webkit", "run")]
    (run,) = form_target_runs(
        "site", "web", {"browser.version": ">=120"}, browsers, engine="chromium"
    )
    assert run.outcome == "run"
    (run,) = form_target_runs(
        "site", "web", {"browser.version": ">=140"}, browsers, engine="chromium"
    )
    assert run.outcome == "not-applicable"
    (run,) = form_target_runs(
        "site", "web", {"browser.version": "120 - 125"}, browsers, engine="chromium"
    )
    assert run.outcome == "failed"
    assert run.reason == 'runsOn.browser.version  "120 - 125"  launched: chromium 130.0.6723.31'
    with pytest.raises(ValueError, match="no browser version"):
        form_target_runs("site", "web", {"browser.version": ">=1"}, [], engine="firefox")


def test_combinations_cover_every_pair_and_two_targets_never_share_a_device() -> None:
    a = tuple(form_target_runs("a", "ios", {"os": ">=17 <19"}, POOL))
    b = tuple(form_target_runs("b", "ios", {"model": IPAD}, POOL))
    combos = combine([Participant(("a",), "ios", a), Participant(("b",), "ios", b)], {"ios": POOL})
    assert [c.label for c in combos] == ["a: iOS 17", "a: iOS 18"]
    for combo in combos:
        assert combo.outcome == "run"
        assert not set(combo.devices["a"]) & set(combo.devices["b"])
    # a's iOS 17 run takes the first model with iOS 17 (SE), b takes iPads.
    assert combos[0].devices == {"a": ("se17",), "b": ("ipad18",)}


def test_same_coordinates_on_one_pool_need_that_many_devices() -> None:
    runs = tuple(form_target_runs("a", "ios", {"model": SE, "os": "18"}, POOL))
    combos = combine(
        [Participant(("a",), "ios", runs), Participant(("b",), "ios", runs)], {"ios": POOL}
    )
    assert combos[0].outcome == "failed"
    assert "no 2 available device(s)" in combos[0].reason


def test_an_unconditioned_target_takes_what_the_conditions_leave() -> None:
    a = tuple(form_target_runs("a", "ios", {"os": "18"}, POOL))
    b = tuple(form_target_runs("b", "ios", {}, POOL))
    (combo,) = combine(
        [Participant(("b",), "ios", b), Participant(("a",), "ios", a)], {"ios": POOL}
    )
    assert combo.devices["a"] == ("se18",)
    assert combo.devices["b"] == ("se17", "ipad17", "ipad18")


def test_a_failed_target_run_fails_the_combination_before_a_not_applicable_one() -> None:
    bad = tuple(form_target_runs("a", "ios", {"model": "iPhone 99"}, POOL))
    skip = tuple(form_target_runs("b", "ios", {"os": ">=19"}, POOL))
    (combo,) = combine([Participant(("a",), "ios", bad), Participant(("b",), "ios", skip)], {})
    assert combo.outcome == "failed"
    (combo,) = combine([Participant(("b",), "ios", skip)], {})
    assert combo.outcome == "not-applicable"


def test_a_web_participant_is_not_narrowed() -> None:
    web = tuple(form_target_runs("site", "web", {"browser.engine": ["chromium"]}, [], engine=None))
    ios = tuple(form_target_runs("app", "ios", {"os": "18"}, POOL))
    (combo,) = combine(
        [Participant(("app",), "ios", ios), Participant(("site",), "playwright", web)],
        {"ios": POOL},
    )
    assert combo.devices == {"app": ("se18",)}
    assert combo.coordinates == {"app": {"os": "iOS 18"}, "site": {"browser.engine": "chromium"}}
    assert combo.label == "app: iOS 18, site: chromium"


def test_first_run_takes_the_first_value_and_the_newest_major_in_range() -> None:
    run, device = first_run("app", "ios", {"model": [IPAD, SE], "os": ">=17 <19"}, POOL)
    assert (run.model, run.major, device and device.udid) == (IPAD, 18, "ipad18")
    with pytest.raises(LookupError, match='os ">=19"'):
        first_run("app", "ios", {"os": ">=19"}, POOL)
    run, device = first_run("site", "web", {"browser.engine": ["webkit", "chromium"]}, [])
    assert (run.engine, device) == ("webkit", None)
    run, device = first_run("app", "ios", {}, POOL)
    assert device == POOL[0]


def test_unpack_conditions_flattens_and_drops_device_state() -> None:
    # A raw group is read one destination block down (BE-0469), keyed by its path under `runsOn`.
    assert unpack_conditions(
        {"ios": {"os": ">=18", "kind": "simulator", "locale": "ja_JP", "seedPhotos": ["a.png"]}}
    ) == {"ios.os": ">=18"}
    assert unpack_conditions({"android": {"apiLevel": 34, "avd": None}}) == {
        "android.apiLevel": "34"
    }
    assert unpack_conditions(
        {"browser": {"engine": "webkit", "version": None, "emulate": "iPhone 13"}}
    ) == {"browser.engine": "webkit"}
    # A flat value is not a destination block, so it names no condition.
    assert unpack_conditions({"os": ">=18"}) == {}


def test_condition_path_names_where_the_target_writes_it() -> None:
    from bajutsu.common.devices.runs import condition_path

    assert condition_path("ios", "model") == "runsOn.ios.model"
    assert condition_path("android", "apiLevel") == "runsOn.android.apiLevel"
    assert condition_path("web", "browser.version") == "runsOn.browser.version"


def test_a_web_union_keeps_each_sides_rule_and_a_bounded_miss_fails() -> None:
    browsers = [DeviceFacts("web-chromium", "web", "chromium", "130.0.6723.31")]

    def outcome(spec: str) -> str:
        (run,) = form_target_runs(
            "site", "web", {"browser.version": spec}, browsers, engine="chromium"
        )
        return run.outcome

    # The open side admits 130, so the union runs.
    assert outcome("120 - 125 || >=128") == "run"
    # Neither side admits 130: the bounded side named a run that must happen, so it fails.
    assert outcome("120 - 125 || >=140") == "failed"
    # Open sides alone say where the scenario applies.
    assert outcome("<100 || >=140") == "not-applicable"


def test_the_eligible_set_falls_through_to_a_later_model_with_enough_at_one_release() -> None:
    # The iPad has two devices but no two at one release, so the SE pair takes the run.
    pool = [
        _ios("a", IPAD, "17.4"),
        _ios("b", IPAD, "17.5"),
        _ios("c", SE, "17.5"),
        _ios("d", SE, "17.5"),
    ]
    (run,) = form_target_runs("app", "ios", {"os": "17"}, pool)
    assert [d.udid for d in eligible_set(run, pool, demand=2) or []] == ["c", "d"]
    # At demand 1 the first model in pool order still wins, at its newest release.
    assert [d.udid for d in eligible_set(run, pool) or []] == ["b"]

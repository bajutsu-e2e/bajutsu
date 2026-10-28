"""The per-route settings table and its check (BE-0467): pure, no device."""

from __future__ import annotations

from dataclasses import replace

import pytest

from bajutsu.common import backends
from bajutsu.common.capability.settings_support import (
    SETTING_KEYS,
    check_settings,
    enforce_settings,
    route_for,
    settings_in_effect,
    settings_support,
)
from bajutsu.common.config import DeviceProvider, Effective, load_config, resolve
from bajutsu.common.config.preconditions import ResolvedPreconditions
from bajutsu.common.drivers import base
from bajutsu.common.platform_lifecycle.environments.web import WebEnvironment

_GRID = "https://grid.example/wd/hub"


def _eff(platform: str, *, lines: tuple[str, ...] = ()) -> Effective:
    app = {"ios": "{id: com.x}", "android": "{id: com.x}", "web": "{url: 'http://x/'}"}[platform]
    text = "\n".join(["targets:", "  t:", f"    platform: {platform}", f"    app: {app}", *lines])
    return resolve(load_config(text + "\n"), "t")


def _device(*lines: str) -> Effective:
    return _eff("ios", lines=("    runsOn: {ios: {kind: device}}", *lines))


def _ignoring(eff: Effective, **reasons: str) -> Effective:
    return replace(eff, run_defaults=replace(eff.run_defaults, ignored_settings=reasons))


# --- the route --- #


def test_each_actuator_resolves_to_its_route() -> None:
    assert route_for("xcuitest", _eff("ios")) == "ios-simulator"
    assert route_for("xcuitest", _device()) == "ios-device"
    assert route_for("xcuitest", _eff("ios"), _GRID) == "ios-live"
    assert route_for("adb", _eff("android")) == "android"
    assert route_for("playwright", _eff("web")) == "web"
    assert route_for("fake", _eff("ios")) is None


def test_an_appium_provider_endpoint_routes_to_the_live_route() -> None:
    # The provider carries the endpoint before any device is acquired (BE-0452).
    eff = replace(_eff("ios"), device_provider=DeviceProvider(kind="appium", endpoint=_GRID))
    assert route_for("xcuitest", eff) == "ios-live"


# --- the table --- #


def test_the_simulator_supports_every_setting_but_the_notifications_grant() -> None:
    table = settings_support("xcuitest", _eff("ios"))
    assert set(table) == set(SETTING_KEYS)
    assert table["permissions.notifications"] == "unsupported"
    assert {k for k, v in table.items() if v != "supported"} == {"permissions.notifications"}


def test_a_real_device_refuses_the_simctl_settings() -> None:
    table = settings_support("xcuitest", _device())
    for key in ("erase", "install", "seedPhotos", "permissions.camera"):
        assert table[key] == "unsupported", key
    for key in ("locale", "launchEnv", "launchArgs", "deeplink"):
        assert table[key] == "supported", key


def test_the_live_route_supports_no_setting_yet() -> None:
    table = settings_support("xcuitest", _eff("ios"), _GRID)
    assert set(table.values()) == {"unsupported"}


def test_android_grants_every_permission_service_but_takes_no_launch_args() -> None:
    table = settings_support("adb", _eff("android"))
    assert all(table[f"permissions.{s}"] == "supported" for s in base.PERMISSION_SERVICES)
    assert table["launchArgs"] == "unsupported"
    assert table["erase"] == table["install"] == table["launchEnv"] == "supported"


def test_the_web_satisfies_erase_and_refuses_launch_values() -> None:
    table = settings_support("playwright", _eff("web"))
    assert table["erase"] == "satisfied"
    assert table["launchEnv"] == table["deeplink"] == table["permissions.camera"] == "unsupported"


def test_a_backend_with_no_route_supports_everything() -> None:
    assert set(settings_support("fake", _eff("ios")).values()) == {"supported"}


def test_erase_precondition_supported_reads_the_table() -> None:
    assert backends.erase_precondition_supported("xcuitest", _eff("ios"))
    assert not backends.erase_precondition_supported("xcuitest", _device())
    assert not backends.erase_precondition_supported("xcuitest", _eff("ios"), _GRID)
    assert backends.erase_precondition_supported("playwright", _eff("web"))


# --- what is in effect --- #


def test_settings_in_effect_lists_what_the_run_asks_for() -> None:
    eff = _eff(
        "ios",
        lines=(
            "    runsOn: {ios: {locale: ja_JP}}",
            "    app: {id: com.x, path: build/App.app, launch: {args: [-x]}}",
        ),
    )
    pre = ResolvedPreconditions(erase=True, launch_env={"A": "1"}, deeplink="app://x")
    assert settings_in_effect(eff, pre, {"camera": "grant"}) == [
        "erase",
        "install",
        "permissions.camera",
        "locale",
        "launchEnv",
        "launchArgs",
        "deeplink",
    ]


def test_a_default_locale_is_not_in_effect() -> None:
    assert settings_in_effect(_eff("ios"), ResolvedPreconditions()) == []
    assert settings_in_effect(_eff("ios"), ResolvedPreconditions(locale="fr_FR")) == ["locale"]


# --- the check --- #


def test_an_unsupported_setting_is_a_problem_naming_its_declaration() -> None:
    check = check_settings("xcuitest", _device(), ResolvedPreconditions(erase=True))
    assert check.unsupported == ["erase"]
    [problem] = check.problems()
    assert "run.erase" in problem and "targets.t.run.ignoredSettings" in problem


def test_a_declared_setting_is_ignored_with_its_reason() -> None:
    eff = _ignoring(_device(), erase="this lane keeps app data")
    check = check_settings("xcuitest", eff, ResolvedPreconditions(erase=True))
    assert check.problems() == []
    assert check.ignored == {"erase": "this lane keeps app data"}


def test_a_declaration_the_route_supports_is_stale() -> None:
    eff = _ignoring(_eff("ios"), erase="no longer true")
    check = check_settings("xcuitest", eff, ResolvedPreconditions())
    assert check.stale == ["erase"]
    assert "supports; remove the declaration" in check.problems()[0]


def test_a_declaration_for_a_satisfied_setting_is_stale_too() -> None:
    eff = _ignoring(_eff("web"), erase="a fresh context already")
    assert check_settings("playwright", eff, ResolvedPreconditions()).stale == ["erase"]


def test_an_unsupported_setting_not_in_effect_is_no_problem() -> None:
    eff = _ignoring(_device(), seedPhotos="curated by hand")
    assert check_settings("xcuitest", eff, ResolvedPreconditions()).problems() == []


# --- the launch-time backstop --- #


def test_enforce_raises_for_an_unsupported_setting() -> None:
    with pytest.raises(base.UnsupportedAction, match=r"app\.launch\.env"):
        enforce_settings("playwright", _eff("web"), ResolvedPreconditions(launch_env={"A": "1"}))


def test_enforce_drops_every_ignored_setting() -> None:
    eff = _ignoring(_device(), erase="r", **{"permissions.camera": "r"})
    pre, permissions = enforce_settings(
        "xcuitest",
        eff,
        ResolvedPreconditions(erase=True, launch_env={"A": "1"}),
        {"camera": "grant"},
    )
    assert not pre.erase
    assert pre.launch_env == {"A": "1"}  # supported on a device, so kept
    assert permissions == {}


def test_the_web_environment_refuses_a_launch_env_it_would_have_ignored() -> None:
    env = WebEnvironment("playwright")
    with pytest.raises(base.UnsupportedAction, match=r"app\.launch\.env"):
        env.start(_eff("web"), ResolvedPreconditions(launch_env={"A": "1"}))


# --- the declaration in the target config --- #


def test_ignored_settings_reach_the_run_defaults() -> None:
    eff = _device("    run: {ignoredSettings: {erase: 'this lane keeps app data'}}")
    assert eff.run_defaults.ignored_settings == {"erase": "this lane keeps app data"}


@pytest.mark.parametrize(
    ("declared", "match"),
    [("{bogus: why}", "unknown setting"), ("{erase: '  '}", "non-blank reason")],
)
def test_a_bad_declaration_fails_at_load(declared: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _device(f"    run: {{ignoredSettings: {declared}}}")


@pytest.mark.parametrize(
    "defaults",
    [
        "  run: {ignoredSettings: {erase: why}}",
        "  platforms: {ios: {run: {ignoredSettings: {erase: why}}}}",
    ],
)
def test_defaults_may_not_declare_an_ignored_setting(defaults: str) -> None:
    text = f"defaults:\n{defaults}\ntargets:\n  t:\n    platform: ios\n    app: {{id: com.x}}\n"
    with pytest.raises(ValueError, match=r"defaults may not set run\.ignoredSettings"):
        load_config(text)


@pytest.mark.parametrize(
    ("key", "pre", "cleared"),
    [
        ("seedPhotos", ResolvedPreconditions(seed_photos=["a.jpg"]), {"seed_photos": []}),
        ("locale", ResolvedPreconditions(locale="ja_JP"), {"locale": None}),
        ("launchEnv", ResolvedPreconditions(launch_env={"A": "1"}), {"launch_env": {}}),
        ("launchArgs", ResolvedPreconditions(launch_args=["-x"]), {"launch_args": []}),
        ("deeplink", ResolvedPreconditions(deeplink="app://x"), {"deeplink": None}),
    ],
)
def test_without_ignored_clears_each_field(
    key: str, pre: ResolvedPreconditions, cleared: dict[str, object]
) -> None:
    from bajutsu.common.capability.settings_support import without_ignored

    dropped, _ = without_ignored(pre, None, [key])
    assert {name: getattr(dropped, name) for name in cleared} == cleared


def test_a_backend_with_no_route_neither_honours_nor_stales_a_declaration() -> None:
    # `--backend fake` runs any target; its real route's declarations do not apply there.
    eff = _ignoring(_device(), erase="keeps app data")
    check = check_settings("fake", eff, ResolvedPreconditions(erase=True))
    assert check.problems() == [] and check.ignored == {}


def test_a_web_target_launch_env_feeds_codegen_and_is_never_in_effect() -> None:
    # The web schema keeps `app.launch.env` for Playwright code generation; no run reads it.
    eff = _eff("web", lines=("    app: {url: 'http://x/', launch: {env: {A: '1'}}}",))
    assert eff.launch_env == {"A": "1"}
    assert settings_in_effect(eff, ResolvedPreconditions()) == []

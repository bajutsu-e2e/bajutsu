"""The platform schema registry (BE-0452 unit 3): one entry per platform, models that forbid a key
from another platform, an unknown-key error that lists the allowed fields, and the three fields unit
7 adds still absent."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from bajutsu.common import backends
from bajutsu.common.config.schema.platform import (
    GROUPS,
    PLATFORM_SCHEMAS,
    Group,
    allowed_fields,
    schema_for,
    validate_group,
)
from bajutsu.common.config.schema.platform.registry import _nested_model


def test_registry_keys_equal_the_platforms() -> None:
    # A platform cannot gain an actuator without registering its schema, nor the reverse.
    assert set(PLATFORM_SCHEMAS) == set(backends.PLATFORMS)


def _models(model: type[BaseModel]) -> list[type[BaseModel]]:
    out = [model]
    for field in model.model_fields.values():
        nested = _nested_model(field.annotation)
        if nested is not None and nested.__module__.startswith("bajutsu.common.config"):
            out.extend(_models(nested))
    return out


@pytest.mark.parametrize("platform", sorted(PLATFORM_SCHEMAS))
@pytest.mark.parametrize("group", GROUPS)
def test_every_registered_model_forbids_extra_keys(platform: str, group: Group) -> None:
    for model in _models(schema_for(platform).model(group)):
        assert model.model_config.get("extra") == "forbid", model.__name__


# The spec's own examples (BE-0452, *The eleven keys*), one group at a time.
@pytest.mark.parametrize(
    ("platform", "group", "data"),
    [
        (
            "ios",
            "app",
            {
                "id": "com.bajutsu.showcase.ios.swiftui",
                "path": "build/Showcase.app",
                "build": "make -C demos/showcase swiftui-build",
                "launch": {"env": {"SHOWCASE_UITEST": "1"}, "args": ["-debug"]},
                "startWhen": {"exists": {"id": "home.title"}},
                "idNamespaces": ["home"],
            },
        ),
        ("ios", "runsOn", {"kind": "simulator", "locale": "en_US"}),
        ("ios", "runsOn", {"model": ["iPhone SE (3rd generation)", "iPad Pro 13-inch (M4)"]}),
        ("ios", "runsOn", {"model": "iPhone 16", "os": ">=17 <19"}),
        ("ios", "driver", {"runner": {"testRunner": "build/Runner.xctestrun"}}),
        ("ios", "run", {"tipKitHandling": True}),
        ("web", "app", {"url": "http://127.0.0.1:8787/index.html"}),
        ("web", "runsOn", {"browser": {"engine": "webkit"}, "emulate": "iPhone 13"}),
        ("web", "runsOn", {"browser": {"engine": ["chromium", "firefox"]}}),
        ("web", "runsOn", {"browser": {"version": ">=120"}}),
        ("web", "driver", {"headless": False}),
        (
            "android",
            "app",
            {"id": "com.example.showcase", "grantPermissions": ["android.permission.CAMERA"]},
        ),
        ("android", "runsOn", {"avd": "Pixel_8", "apiLevel": ">=33"}),
        ("android", "runsOn", {"avd": ["Pixel_8", "Pixel_Tablet"], "apiLevel": "33 - 35"}),
        ("android", "driver", {"nativeZ": True}),
        ("fake", "app", {}),
        ("fake", "app", {"id": "anything"}),
        ("fake", "app", {"idNamespaces": ["home"]}),
        ("fake", "runsOn", {}),
        ("fake", "driver", None),
    ],
)
def test_spec_examples_validate(platform: str, group: Group, data: Any) -> None:
    validate_group(platform, group, data, where="targets.t." + group)


def test_defaults_are_what_an_omitted_block_means() -> None:
    runs_on: Any = validate_group("web", "runsOn", None, where="targets.t.runsOn")
    assert (runs_on.browser.engine, runs_on.emulate) == ("chromium", "desktop")
    ios: Any = validate_group("ios", "runsOn", {}, where="targets.t.runsOn")
    assert (ios.kind, ios.model, ios.os) == ("simulator", None, None)
    assert validate_group("web", "driver", {}, where="x").headless is True  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("platform", "group", "data", "where", "allowed"),
    [
        # a key from another platform
        (
            "ios",
            "runsOn",
            {"browser": {"engine": "webkit"}},
            "runsOn.browser",
            "model, os, kind, locale, seedPhotos",
        ),
        ("web", "runsOn", {"apiLevel": "34"}, "runsOn.apiLevel", "browser, emulate"),
        ("android", "driver", {"headless": True}, "driver.headless", "nativeZ"),
        ("web", "app", {"id": "x", "url": "u"}, "app.id", "startWhen, idNamespaces, url, launch"),
        # a nested unknown key lists the nested block's fields
        (
            "ios",
            "driver",
            {"runner": {"testrunner": "x"}},
            "driver.runner.testrunner",
            "testRunner, build, hostAddress",
        ),
        (
            "web",
            "runsOn",
            {"browser": {"engin": "webkit"}},
            "runsOn.browser.engin",
            "engine, version",
        ),
        (
            "android",
            "app",
            {"id": "p", "launch": {"args": ["-x"]}},
            "app.launch.args",
            "env, deeplink",
        ),
        # the iOS run extension, and a platform with none
        ("android", "run", {"tipKitHandling": True}, "run.tipKitHandling", "(none)"),
        ("fake", "runsOn", {"model": "iPhone 16"}, "runsOn.model", "(none)"),
    ],
)
def test_unknown_key_error_lists_the_allowed_fields(
    platform: str, group: Group, data: Any, where: str, allowed: str
) -> None:
    with pytest.raises(ValueError, match="unknown field") as caught:
        validate_group(platform, group, data, where="targets.t." + group)
    assert not isinstance(caught.value, ValidationError)
    assert str(caught.value) == (
        f"targets.t.{where}: unknown field for platform {platform!r}; allowed fields: {allowed}"
    )


def test_every_unknown_key_is_reported() -> None:
    with pytest.raises(ValueError, match="unknown field") as caught:
        validate_group("ios", "runsOn", {"avd": "x", "emulate": "y"}, where="targets.t.runsOn")
    assert [line.split(":")[0] for line in str(caught.value).splitlines()] == [
        "targets.t.runsOn.avd",
        "targets.t.runsOn.emulate",
    ]


@pytest.mark.parametrize(
    ("platform", "group", "data", "where"),
    [
        ("ios", "app", {"id": "b", "reinstall": "overwrite"}, "app.reinstall"),
        (
            "ios",
            "app",
            {"id": "b", "launch": {"deeplink": "showcase://cart"}},
            "app.launch.deeplink",
        ),
        ("android", "app", {"id": "p", "launch": {"deeplink": "x://y"}}, "app.launch.deeplink"),
        ("ios", "runsOn", {"seedPhotos": ["photos/cat.jpg"]}, "runsOn.seedPhotos"),
    ],
)
def test_unit_seven_fields_load(platform: str, group: Group, data: Any, where: str) -> None:
    # Unit 7 added these with their `Effective` attributes, so each now loads on its platform.
    validate_group(platform, group, data, where="targets.t." + group)


def test_other_failures_keep_pydantics_error() -> None:
    with pytest.raises(ValidationError, match="id"):
        validate_group("ios", "app", {"path": "x.app"}, where="targets.t.app")


@pytest.mark.parametrize(
    ("platform", "group", "data", "message"),
    [
        ("ios", "app", {"id": ""}, "at least 1 character"),
        ("web", "app", {"url": ""}, "at least 1 character"),
        ("android", "app", {"package": "p"}, r"targets\.t\.app\.id: Field required\n.*package"),
        ("ios", "runsOn", {"os": ">=17.0.0-beta"}, "prerelease tags are not used"),
        ("ios", "runsOn", {"os": "latest"}, "invalid version range"),
        ("android", "runsOn", {"apiLevel": "thirty"}, "invalid version range"),
        ("web", "runsOn", {"browser": {"version": "~"}}, "invalid version range"),
        ("ios", "runsOn", {"model": []}, "runsOn.model lists no value"),
        ("android", "runsOn", {"avd": ["a", "a"]}, "runsOn.avd lists a value twice"),
        ("web", "runsOn", {"browser": {"engine": "safari"}}, "invalid browser 'safari'"),
        ("web", "runsOn", {"browser": {"engine": ["webkit", "edge"]}}, "invalid browser 'edge'"),
        (
            "web",
            "runsOn",
            {"browser": {"engine": ["chromium", "webkit"], "version": ">=120"}},
            "cannot be combined with a list of engines",
        ),
        ("ios", "runsOn", {"kind": "tablet"}, "simulator"),
        (
            "ios",
            "runsOn",
            {"kind": "device", "model": "iPhone 16"},
            "runsOn.model is a device condition",
        ),
        ("ios", "runsOn", {"kind": "device", "os": ">=18"}, "runsOn.os is a device condition"),
        (
            "ios",
            "runsOn",
            {"kind": "device", "model": ["a", "b"]},
            "runsOn.model is a device condition",
        ),
        (
            "ios",
            "app",
            {"id": "b", "startWhen": {"exists": {"id": "x", "negate": True}}},
            "cannot set negate",
        ),
        ("ios", "app", {"id": "b", "startWhen": {"value": {"id": "x", "equals": "y"}}}, "value"),
        ("ios", "app", {"id": "b", "startWhen": {"exists": {"id": []}}}, "id"),
    ],
)
def test_invalid_values_fail_at_load(platform: str, group: Group, data: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_group(platform, group, data, where="targets.t." + group)


def test_a_physical_device_keeps_its_device_state() -> None:
    # `locale` is device state, not a condition, so `kind: device` still accepts it.
    runs_on: Any = validate_group(
        "ios", "runsOn", {"kind": "device", "locale": "ja_JP", "model": None}, where="x"
    )
    assert (runs_on.kind, runs_on.locale) == ("device", "ja_JP")


def test_unknown_platform_lists_the_registered_ones() -> None:
    with pytest.raises(
        ValueError, match="invalid platform 'flutter': use one of ios, android, web, fake"
    ):
        validate_group("flutter", "app", {}, where="targets.t.app")


def test_allowed_fields_spells_aliases() -> None:
    assert allowed_fields(schema_for("android").app) == (
        "startWhen",
        "idNamespaces",
        "id",
        "path",
        "build",
        "reinstall",
        "launch",
        "grantPermissions",
    )


def test_the_target_validates_its_groups_with_the_registered_models() -> None:
    # Unit 4 wires the registry in: each platform-shaped group is an instance of its platform's model.
    from bajutsu.common.config.schema import WEB_ENGINES, TargetConfig
    from bajutsu.common.config.schema.platform.web import WebApp, WebRunsOn

    target = TargetConfig.model_validate(
        {"platform": "web", "app": {"url": "http://x"}, "runsOn": {"browser": {"engine": "webkit"}}}
    )
    assert isinstance(target.app, WebApp)
    assert isinstance(target.runs_on, WebRunsOn)
    assert target.runs_on.browser.engine == "webkit"
    assert WEB_ENGINES == ("chromium", "firefox", "webkit")


def test_an_unknown_key_in_a_target_group_lists_the_allowed_fields() -> None:
    from pydantic import ValidationError

    from bajutsu.common.config import load_config

    with pytest.raises(ValidationError, match=r"runsOn\.browser: unknown field for platform 'ios'"):
        load_config(
            "targets:\n  s: { platform: ios, app: { id: com.x }, runsOn: { browser: webkit } }\n"
        )


def test_the_run_group_lists_the_shared_and_the_platform_fields() -> None:
    from pydantic import ValidationError

    from bajutsu.common.config import load_config

    with pytest.raises(ValidationError, match=r"allowed fields: erase, network.*tipKitHandling"):
        load_config("targets:\n  s: { platform: ios, app: { id: com.x }, run: { bogus: 1 } }\n")
    # A field of another platform's run extension is unknown on this one.
    with pytest.raises(ValidationError, match=r"run\.tipKitHandling: unknown field for platform"):
        load_config(
            "targets:\n  s: { platform: web, app: { url: 'http://x/' }, "
            "run: { tipKitHandling: true } }\n"
        )


@pytest.mark.parametrize(
    ("platform", "data", "read"),
    [
        ("android", {"apiLevel": 34}, lambda m: m.api_level),
        ("ios", {"os": 18}, lambda m: m.os),
        ("web", {"browser": {"version": 120}}, lambda m: m.browser.version),
    ],
)
def test_an_unquoted_integer_range_reads_as_its_text(platform: str, data: Any, read: Any) -> None:
    model = validate_group(platform, "runsOn", data, where="targets.t.runsOn")
    assert read(model) in ("34", "18", "120")


@pytest.mark.parametrize(
    ("platform", "data"),
    [
        ("ios", {"os": 18.10}),
        ("android", {"apiLevel": 34.0}),
        ("web", {"browser": {"version": 120.5}}),
    ],
)
def test_an_unquoted_decimal_range_asks_for_quotes(platform: str, data: Any) -> None:
    with pytest.raises(ValueError, match=r'is a YAML number; quote it .*os: "18\.10"'):
        validate_group(platform, "runsOn", data, where="targets.t.runsOn")

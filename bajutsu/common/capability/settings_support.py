"""Which environment-specific settings each environment route can apply (BE-0467).

A setting such as `run.erase` or `runsOn.ios.seedPhotos` reaches the device before the first step,
and not every route can apply it: `simctl erase` reaches the Simulator and nothing else. Each route
used to handle the gap its own way — a raise at launch, a silent ignore, or a BE-0082 preflight
failure. This module is the one table they all read instead, so the run preflight, each
environment's launch, and (later) the lint and the serve dispatch cannot disagree.

Every setting is in one of three states per route:

- `supported` — the route applies it.
- `satisfied` — the route already guarantees what it asks for (a web run's fresh browser context is
  already erased), so it is never an error.
- `unsupported` — the route cannot apply it. The run fails before any device work unless the target
  declares the setting under `run.ignoredSettings` with a reason; a declared setting is not applied.

A declaration for a setting the route supports or satisfies is itself an error, so a stale one
cannot hide a regression once the route gains the setting.

Pure: no device, no clock, no network, so the check sits on the deterministic path (prime directive 1).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from bajutsu.common.drivers import base

if TYPE_CHECKING:
    from bajutsu.common.config import Effective
    from bajutsu.common.config.preconditions import ResolvedPreconditions

_logger = logging.getLogger(__name__)

SettingSupport = Literal["supported", "satisfied", "unsupported"]

# The environment routes a target resolves to: the columns of BE-0467's table. The three iOS routes
# are separate because they support different settings.
Route = Literal["ios-simulator", "ios-device", "ios-live", "android", "web"]

_PERMISSION_PREFIX = "permissions."

# Each setting key, as `run.ignoredSettings` names it, with the source field a reader recognizes.
SETTING_FIELDS: dict[str, str] = {
    "erase": "run.erase",
    "install": "app.path install",
    **{
        f"{_PERMISSION_PREFIX}{service}": f"permissions.{service}"
        for service in base.PERMISSION_SERVICES
    },
    "seedPhotos": "runsOn.ios.seedPhotos",
    "locale": "runsOn.ios.locale",
    "launchEnv": "app.launch.env",
    "launchArgs": "app.launch.args",
    "deeplink": "app.launch.deeplink",
}
SETTING_KEYS: tuple[str, ...] = tuple(SETTING_FIELDS)

_S: SettingSupport = "supported"
_SAT: SettingSupport = "satisfied"
_U: SettingSupport = "unsupported"

# Every key but the per-service permissions, which follow the route's capability tokens instead.
# Android has no `app.launch.args` and `am start` takes none, and it has no iOS photo library or
# locale; the loader keeps those fields off an Android or web target, so their rows only matter for a
# scenario that sets one while driving a second, iOS target.
_ROWS: dict[Route, dict[str, SettingSupport]] = {
    "ios-simulator": {
        "erase": _S,
        "install": _S,
        "seedPhotos": _S,
        "locale": _S,
        "launchEnv": _S,
        "launchArgs": _S,
        "deeplink": _S,
    },
    # simctl cannot reach a physical device, and its build is installed out of band (BE-0238). The
    # locale travels to the app as a launch argument, which a device receives like any other.
    "ios-device": {
        "erase": _U,
        "install": _U,
        "seedPhotos": _U,
        "locale": _S,
        "launchEnv": _S,
        "launchArgs": _S,
        "deeplink": _S,
    },
    # What `xcuitest_live.py` wires today; each one BE-0238 wires flips its row to `supported`.
    "ios-live": {
        "erase": _U,
        "install": _U,
        "seedPhotos": _U,
        "locale": _U,
        "launchEnv": _U,
        "launchArgs": _U,
        "deeplink": _U,
    },
    "android": {
        "erase": _S,
        "install": _S,
        "seedPhotos": _U,
        "locale": _U,
        "launchEnv": _S,
        "launchArgs": _U,
        "deeplink": _S,
    },
    # Every web run starts a fresh browser context, so `erase: true` already holds.
    "web": {
        "erase": _SAT,
        "install": _U,
        "seedPhotos": _U,
        "locale": _U,
        "launchEnv": _U,
        "launchArgs": _U,
        "deeplink": _U,
    },
}


def route_for(actuator: str | None, eff: Effective, udid_spec: str = "booted") -> Route | None:
    """The environment route *actuator* drives for *eff*, or None for a backend with no table.

    Uses the two predicates `capabilities_for_run` narrows on — a WebDriver endpoint, then a real
    device — so the route and the capability set always describe the same environment. The `fake`
    backend has no route: it applies nothing, so it has nothing to refuse.
    """
    if actuator == "adb":
        return "android"
    if actuator == "playwright":
        return "web"
    if actuator != "xcuitest":
        return None
    from bajutsu.common.config import xcuitest_targets_real_device
    from bajutsu.common.platform_lifecycle.environments.xcuitest_live import is_webdriver_endpoint

    provider = eff.device_provider
    if provider is not None and provider.kind == "appium" and provider.endpoint:
        udid_spec = provider.endpoint.strip()
    if is_webdriver_endpoint(udid_spec):
        return "ios-live"
    if xcuitest_targets_real_device(eff):
        return "ios-device"
    return "ios-simulator"


def settings_support(
    actuator: str | None, eff: Effective, udid_spec: str = "booted"
) -> dict[str, SettingSupport]:
    """How the route *actuator* drives for *eff* handles each setting key.

    A permission service is `supported` exactly when the route advertises its BE-0276 token, so the
    capability set stays the one statement of which services a backend grants. A backend with no
    route supports every key, which is what `erase_precondition_supported` reads for it.
    """
    route = route_for(actuator, eff, udid_spec)
    if route is None or actuator is None:
        return dict.fromkeys(SETTING_KEYS, _S)
    from bajutsu.common.backends import capabilities_for_run

    caps = capabilities_for_run(actuator, eff, udid_spec)
    permissions: dict[str, SettingSupport] = {
        f"{_PERMISSION_PREFIX}{service}": (
            _S if base.permission_capability(service) in caps else _U
        )
        for service in base.PERMISSION_SERVICES
    }
    return {**_ROWS[route], **permissions}


def settings_in_effect(
    eff: Effective, pre: ResolvedPreconditions, permissions: Mapping[str, str] | None = None
) -> list[str]:
    """The setting keys this run asks the environment to apply, in `SETTING_KEYS` order.

    A key is in effect when its field would change what reaches the device: a permission the
    scenario names, a launch value the target or the scenario sets, a build path to install.
    """
    from bajutsu.common.config import AndroidConfig, IosConfig
    from bajutsu.common.config.resolve import DEFAULT_LOCALE
    from bajutsu.common.config.schema.platform import accepts_field, schema_for

    platform = eff.platform_config
    # A scenario's `app.launch` values apply to the targets whose platform has the field (BE-0452):
    # an iOS-and-web scenario's deeplink is meant for the iOS app, never the browser, so it is not in
    # effect on the web target. The target's own values only exist where its platform has them.
    app_schema = schema_for(eff.schema_platform).app

    def launch(name: str) -> bool:
        return accepts_field(app_schema, ("launch", name))

    native = isinstance(platform, IosConfig | AndroidConfig)
    app_path = platform.app_path if isinstance(platform, IosConfig | AndroidConfig) else None
    # `Effective.locale` always holds a value, the built-in default when the target names none, so
    # only a value that differs from that default — or the scenario's own — counts as asked for.
    locale = bool(pre.locale) or (isinstance(platform, IosConfig) and eff.locale != DEFAULT_LOCALE)
    asked = {
        "erase": pre.erase,
        "install": bool(app_path),
        **{f"{_PERMISSION_PREFIX}{service}": True for service in permissions or {}},
        "seedPhotos": bool(pre.seed_photos),
        "locale": locale,
        # A web target's own `app.launch.env` feeds Playwright code generation, never a run, so only
        # an app platform's own launch values are asked of the device; a scenario's still count.
        "launchEnv": (native and _user_env(eff.launch_env))
        or (launch("env") and _user_env(pre.launch_env)),
        "launchArgs": (native and bool(eff.launch_args))
        or (launch("args") and bool(pre.launch_args)),
        "deeplink": launch("deeplink") and pre.deeplink is not None,
    }
    return [key for key in SETTING_KEYS if asked.get(key)]


# The prefix of the keys Bajutsu writes into a launch env for its own SDK protocol (`BAJUTSU_MOCKS`,
# `BAJUTSU_TOUCH_MARKERS`, `BAJUTSU_CONTROL_CHANNEL`). The run sets them on every platform, and a
# route that has no app to read them (the web applies mocks natively) loses nothing, so they are
# Bajutsu's own injection, like the live route's `extra_env`, not a setting the target asked for.
_BAJUTSU_ENV_PREFIX = "BAJUTSU_"


def _user_env(env: Mapping[str, str]) -> bool:
    """Whether *env* holds a key the scenario or target set, beyond Bajutsu's own protocol keys."""
    return any(not key.startswith(_BAJUTSU_ENV_PREFIX) for key in env)


@dataclass(frozen=True)
class SettingsCheck:
    """One target's settings judged against its route's table.

    Attributes:
        target: The target name.
        route: The route the target resolves to; None for a backend with no table.
        unsupported: Keys in effect that the route cannot apply and the target does not ignore.
        ignored: Keys in effect that the route cannot apply and the target ignores, with the reason.
        stale: Keys the target ignores although the route supports or satisfies them.
    """

    target: str
    route: Route | None
    unsupported: list[str] = field(default_factory=list)
    ignored: dict[str, str] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)

    def unsupported_problems(self) -> list[str]:
        """One message per unsupported key, naming the declaration that would ignore it."""
        return [
            f"{SETTING_FIELDS[key]} is not supported on target '{self.target}' (route "
            f"{self.route}); remove it, or declare '{key}' with a reason under "
            f"targets.{self.target}.run.ignoredSettings to run without it"
            for key in self.unsupported
        ]

    def problems(self) -> list[str]:
        """One message per unsupported or stale key; empty when the target can run."""
        return self.unsupported_problems() + [
            f"targets.{self.target}.run.ignoredSettings declares '{key}', which target "
            f"'{self.target}' (route {self.route}) supports; remove the declaration"
            for key in self.stale
        ]


def check_settings(
    actuator: str | None,
    eff: Effective,
    pre: ResolvedPreconditions,
    permissions: Mapping[str, str] | None = None,
    udid_spec: str = "booted",
) -> SettingsCheck:
    """Judge the settings this run puts in effect against the route *actuator* drives for *eff*.

    Args:
        actuator: The actuator the target runs on.
        eff: The target's effective config, carrying its `run.ignoredSettings`.
        pre: The scenario's preconditions merged over the target's.
        permissions: The scenario's `permissions` field.
        udid_spec: The device spec the run resolves, which names a WebDriver endpoint on that route.
    """
    route = route_for(actuator, eff, udid_spec)
    if route is None:
        # A backend with no table (`--backend fake`) applies nothing, so a declaration made for the
        # target's real route is neither honoured nor stale here.
        return SettingsCheck(target=eff.target, route=None)
    table = settings_support(actuator, eff, udid_spec)
    declared = eff.run_defaults.ignored_settings
    unsupported: list[str] = []
    ignored: dict[str, str] = {}
    for key in settings_in_effect(eff, pre, permissions):
        if table[key] != "unsupported":
            continue
        if key in declared:
            ignored[key] = declared[key]
        else:
            unsupported.append(key)
    stale = [key for key in SETTING_KEYS if key in declared and table[key] != "unsupported"]
    return SettingsCheck(
        target=eff.target,
        route=route,
        unsupported=unsupported,
        ignored=ignored,
        stale=stale,
    )


def without_ignored(
    pre: ResolvedPreconditions, permissions: Mapping[str, str] | None, ignored: Sequence[str]
) -> tuple[ResolvedPreconditions, dict[str, str]]:
    """*pre* and *permissions* with every ignored setting dropped, so the environment applies none.

    A launch value the target itself sets stays on `Effective`; only a route that cannot apply it
    ignores it, and that route never reads it. Bajutsu's own launch keys are not the setting, so an
    ignored `launchEnv` keeps them for the route to apply or refuse.
    """
    skip = set(ignored)
    kept = {
        service: state
        for service, state in (permissions or {}).items()
        if f"{_PERMISSION_PREFIX}{service}" not in skip
    }
    return (
        replace(
            pre,
            erase=pre.erase and "erase" not in skip,
            seed_photos=[] if "seedPhotos" in skip else pre.seed_photos,
            locale=None if "locale" in skip else pre.locale,
            launch_env=(
                {k: v for k, v in pre.launch_env.items() if k.startswith(_BAJUTSU_ENV_PREFIX)}
                if "launchEnv" in skip
                else pre.launch_env
            ),
            launch_args=[] if "launchArgs" in skip else pre.launch_args,
            deeplink=None if "deeplink" in skip else pre.deeplink,
        ),
        kept,
    )


def enforce_settings(
    actuator: str | None,
    eff: Effective,
    pre: ResolvedPreconditions,
    permissions: Mapping[str, str] | None = None,
    udid_spec: str = "booted",
) -> tuple[ResolvedPreconditions, dict[str, str]]:
    """The launch-time backstop: refuse an unsupported setting, and drop every ignored one.

    The run preflight makes the same check before any device work, but a lease driven directly skips
    it, so each environment calls this as it launches rather than keeping its own conditions.

    Raises:
        base.UnsupportedAction: A setting in effect is unsupported here and the target does not
            ignore it.
    """
    check = check_settings(actuator, eff, pre, permissions, udid_spec)
    if check.unsupported:
        raise base.UnsupportedAction("; ".join(check.unsupported_problems()))
    for key, reason in check.ignored.items():
        # The report does not list an ignored setting yet (BE-0467 unit 4), so the log is the one
        # place a reader can see that the run went without it.
        _logger.warning(
            "target %r runs without %s (run.ignoredSettings): %s",
            check.target,
            SETTING_FIELDS[key],
            reason,
        )
    return without_ignored(pre, permissions, list(check.ignored))

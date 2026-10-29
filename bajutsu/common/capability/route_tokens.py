"""Routing tokens for what each environment route can run and apply (BE-0467).

serve routes a job by capability tokens (BE-0166). Two kinds name an environment route, one of the
columns of the settings table (`settings_support.Route`):

- `cap:<route>:<capability>` — a step routed to a target on `<route>` needs the capability; a worker
  that drives `<route>` advertises every capability the route has.
- `setting:<route>:<key>` — a setting is in effect on a target on `<route>` that does not declare it
  ignored; a worker that drives `<route>` advertises every key the route supports or satisfies.

A platform alone is not enough, since the three iOS routes differ: a Mac serving the Simulator and an
attached iPhone advertises both routes' sets, so it never takes an `erase` job on the device route
and never turns away a Simulator job.

Both sides read the support table and the BE-0082 requirement table, the same two the run
preflight judges, so a job a worker can lease is one its run will accept. Pure: no device.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, get_args

from bajutsu.common.capability import capability_preflight
from bajutsu.common.capability.settings_support import (
    Route,
    route_for,
    route_settings,
    settings_in_effect,
)
from bajutsu.common.capability.worker_capability import (
    CAP_ROUTING_PREFIX,
    SETTING_ROUTING_PREFIX,
)
from bajutsu.common.drivers import base

if TYPE_CHECKING:
    from bajutsu.common.config import Effective
    from bajutsu.common.scenario import Scenario

ROUTES: tuple[Route, ...] = get_args(Route)

# Advertised by every worker that computes route tokens, even one whose drivers drive no route (a
# `fake`-only worker), so serve tells it apart from a worker built before this item, which it matches
# on today's tokens alone. No job ever requires it.
ROUTE_AWARE_TOKEN = f"{CAP_ROUTING_PREFIX}routing"

# The provenance entry serve stamps on a job a worker from before this item ran: such a worker
# matches on today's tokens alone and keeps no settings check, so the report says so.
UNCHECKED_PROVENANCE_KEY = "workerRouting"
UNCHECKED_PROVENANCE_VALUE = "unchecked"
UNCHECKED_NOTICE = (
    "this job ran on a worker that advertises no step capabilities or settings (BE-0467), so "
    "dispatch did not check them; a worker older still also skips the run's settings check"
)


def report_notices(provenance: Mapping[str, object] | None) -> list[str]:
    """The run-level notices a report shows for its manifest *provenance* (BE-0467)."""
    if (provenance or {}).get(UNCHECKED_PROVENANCE_KEY) == UNCHECKED_PROVENANCE_VALUE:
        return [UNCHECKED_NOTICE]
    return []


# Which route each actuator drives on a worker's own host. `fake` drives none: it applies nothing,
# so a job on it needs no route token.
_ACTUATOR_ROUTE: dict[str, Route] = {
    "xcuitest": "ios-simulator",
    "adb": "android",
    "playwright": "web",
}


def cap_token(route: str, capability: str) -> str:
    """The token for *capability* on *route*, e.g. ``cap:ios-simulator:deviceControl.setLocation``."""
    return f"{CAP_ROUTING_PREFIX}{route}:{capability}"


def setting_token(route: str, key: str) -> str:
    """The token for setting *key* on *route*, e.g. ``setting:ios-device:locale``."""
    return f"{SETTING_ROUTING_PREFIX}{route}:{key}"


def is_route_token(token: str) -> bool:
    """Whether *token* is a `cap:` or `setting:` token."""
    return token.startswith((CAP_ROUTING_PREFIX, SETTING_ROUTING_PREFIX))


def token_route(token: str) -> str | None:
    """The route a `cap:` or `setting:` token names, or None for any other token."""
    if not is_route_token(token):
        return None
    route = token.split(":", 2)[1]
    # `ROUTE_AWARE_TOKEN` carries the prefix but names no route.
    return route if route in ROUTES else None


def route_tokens(route: Route) -> set[str]:
    """Every token a worker driving *route* advertises."""
    from bajutsu.common.backends import capabilities_for_route

    caps = {cap_token(route, c) for c in capabilities_for_route(route)}
    settings = {
        setting_token(route, key)
        for key, support in route_settings(route).items()
        if support != "unsupported"
    }
    return caps | settings


def worker_routes(
    actuators: Iterable[str], *, real_devices: bool | None = None, appium: bool = False
) -> list[Route]:
    """The routes a worker drives, in `ROUTES` order.

    Args:
        actuators: The drivers the worker can run on its own host.
        real_devices: Whether its `worker.yaml` says it drives attached real iPhones too; None (not
            said) counts as yes, so a worker that served real-device jobs before still does.
        appium: Whether it drives an Appium grid, which is the live WebDriver route.
    """
    routes = {_ACTUATOR_ROUTE[a] for a in actuators if a in _ACTUATOR_ROUTE}
    if real_devices is not False and "xcuitest" in actuators:
        routes.add("ios-device")
    if appium:
        routes.add("ios-live")
    return [r for r in ROUTES if r in routes]


def advertised_route_tokens(routes: Iterable[Route]) -> set[str]:
    """The union of `route_tokens` over *routes*, one set per route and never intersected, plus
    `ROUTE_AWARE_TOKEN`."""
    out: set[str] = {ROUTE_AWARE_TOKEN}
    for route in routes:
        out |= route_tokens(route)
    return out


def target_route_needs(
    s: Scenario,
    name: str,
    actuator: str | None,
    eff: Effective,
    *,
    routed: bool,
    grouped: bool,
    udid_spec: str = "booted",
    grid: bool = False,
) -> dict[str, list[str]]:
    """The tokens one target of *s* needs, each with the constructs or settings that need it.

    Args:
        s: The expanded scenario, with its lifecycle hooks folded in.
        name: The target's name.
        actuator: The actuator the target runs on; None or `fake` needs no token.
        eff: The target's effective config, carrying its `run.ignoredSettings`.
        routed: Whether *s* declares its targets, so only the steps naming *name* count.
        grouped: Whether *name* shares a device with another target (BE-0447).
        udid_spec: The device spec, which names a WebDriver endpoint on the live route.
        grid: Whether the job runs on an Appium grid, whose worker supplies the endpoint an iOS
            target's config does not name; such a target is on the live route.
    """
    from bajutsu.common.config.preconditions import resolve_preconditions

    route = route_for(actuator, eff, udid_spec)
    if route is None:
        return {}
    if grid and route in ("ios-simulator", "ios-device"):
        route = "ios-live"
    narrowed = capability_preflight.steps_for_target(s, name) if routed else s
    needs = {
        cap_token(route, cap): why
        for cap, why in capability_preflight.requirements(narrowed).items()
    }
    if grouped:
        needs[cap_token(route, base.Capability.DEVICE_GROUP)] = [f"target '{name}': a device group"]
    declared = eff.run_defaults.ignored_settings
    pre = resolve_preconditions(eff, s.preconditions)
    for key in settings_in_effect(eff, pre, s.permissions):
        if key not in declared:
            needs[setting_token(route, key)] = [f"target '{name}': setting {key}"]
    return needs

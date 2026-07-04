"""Preflight capability check (BE-0082).

Every backend declares what it can do via `Driver.capabilities()`. A scenario can ask for an
action the chosen backend can't perform — a two-finger pinch on idb (single-touch). Before this
check, the only gate was `gestures.py`'s `_require_multi_touch`, fired mid-run, so a scenario
whose last step needed an unsupported capability ran every earlier step on a device first, then
failed late. `unsupported()` moves that check up front: it is a pure function of (scenario,
capability set), so the runner can fail a scenario *before* any device work, deterministically and
with one aggregated message (prime directive #2: fail fast and clearly).

The map gates only the **true hard requirements** the capability set cleanly decides:

- `pinch` / `rotate` need `multiTouch`.
- a `visual` assertion needs `screenshot`.
- a device-control step (`setLocation` / `push` / `clearKeychain` / `clearClipboard` /
  `setClipboard` / `background` / `foreground` / `overrideStatusBar` / `clearStatusBar`) needs
  `deviceControl` (BE-0128) — the whole simctl-backed `DeviceControl` family as one unit.
  `relaunch` is not here: it is gated by the injected relauncher, not `DeviceControl`.
- every run needs `query` + `elements` (the baseline read path).

Deliberately **not** gated (an audit of what each construct actually depends on, BE-0082):

- `conditionWait` — the orchestrator implements every wait by polling `query()` / the network
  collector (`orchestrator/waits.py`), so no backend needs the capability; gating it would reject
  scenarios that run fine.
- `network` — idb does not advertise `network` (that token means *native* observation, which
  Playwright has), yet idb still captures traffic through the app-side collector, so a `request` /
  `event` / `requestSequence` / `responseSchema` assertion or `until: { request }` wait runs on
  idb. Gating on the capability would wrongly reject those.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Set
from dataclasses import dataclass

from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Scenario, Step


# A construct the capability set can gate: the capability it needs, a human label, and a
# function that returns every scenario path where the construct appears. One reason per
# occurrence, so the user sees exactly where the unsupported construct lives.
@dataclass(frozen=True)
class _Requirement:
    capability: str
    label: str
    locations: Callable[[Scenario], list[str]]


def _walk_steps(steps: list[Step], prefix: str = "") -> Iterator[tuple[str, Step]]:
    """Every step with its human-readable path, recursing into `if` / `forEach`.

    Those blocks run at runtime (only `use` is expanded away before the run), so a construct
    nested inside them must still be seen — otherwise the preflight misses it and the run
    fails late, the very thing it exists to prevent.
    """
    for i, step in enumerate(steps):
        path = f"{prefix}step {i + 1}" if not prefix else f"{prefix}[{i}]"
        yield path, step
        if step.if_ is not None:
            yield from _walk_steps(step.if_.then, f"{path} > if > then")
            yield from _walk_steps(step.if_.else_ or [], f"{path} > if > else")
        if step.for_each is not None:
            yield from _walk_steps(step.for_each.steps, f"{path} > forEach")


def _assertions_with_path(scenario: Scenario) -> Iterator[tuple[str, Assertion]]:
    """Every assertion with its path across the whole step tree.

    Covers the scenario `expect`, each step's `assert`, and every `if` condition.
    """
    for i, a in enumerate(scenario.expect or []):
        yield f"expect[{i}]", a
    for step_path, step in _walk_steps(scenario.steps):
        for j, a in enumerate(step.assert_ or []):
            yield f"{step_path} > assert[{j}]", a
        if step.if_ is not None:
            yield f"{step_path} > if > condition", step.if_.condition


def _multi_touch_locations(sc: Scenario) -> list[str]:
    """The paths where a two-finger gesture (pinch/rotate) appears."""
    return [
        path
        for path, step in _walk_steps(sc.steps)
        if step.pinch is not None or step.rotate is not None
    ]


def _visual_locations(sc: Scenario) -> list[str]:
    """The paths where a visual assertion appears."""
    return [path for path, a in _assertions_with_path(sc) if a.visual is not None]


def _device_control_locations(sc: Scenario) -> list[str]:
    """The paths where a device-control step appears (any `DeviceControl` op; not `relaunch`)."""
    return [
        path
        for path, step in _walk_steps(sc.steps)
        if step.set_location is not None
        or step.push is not None
        or step.clear_keychain is not None
        or step.clear_clipboard is not None
        or step.set_clipboard is not None
        or step.background is not None
        or step.foreground is not None
        or step.override_status_bar is not None
        or step.clear_status_bar is not None
    ]


# Capabilities every run needs regardless of which constructs it uses (the baseline read path).
_BASELINE = (base.Capability.QUERY, base.Capability.ELEMENTS)

_REQUIREMENTS = (
    _Requirement(
        base.Capability.MULTI_TOUCH,
        "pinch / rotate (two-finger gesture)",
        _multi_touch_locations,
    ),
    _Requirement(
        base.Capability.SCREENSHOT,
        "visual assertion",
        _visual_locations,
    ),
    _Requirement(
        base.Capability.DEVICE_CONTROL,
        "device-control step (simctl-backed)",
        _device_control_locations,
    ),
)


def unsupported(scenario: Scenario, capabilities: Set[str]) -> list[str]:
    """The reasons `scenario` can't run on a backend with `capabilities`.

    One reason per unsupported construct occurrence (with its scenario path), empty when it is
    runnable. Pure: no device, no clock, no network. `capabilities` is any set type (the runner
    passes the driver's frozen `CAPABILITIES` directly).
    """
    reasons = [f"running needs '{cap}'" for cap in _BASELINE if cap not in capabilities]
    for req in _REQUIREMENTS:
        if req.capability not in capabilities:
            reasons.extend(
                f"{path}: {req.label} needs '{req.capability}'" for path in req.locations(scenario)
            )
    return reasons

"""Preflight capability check (BE-0082).

Every backend declares what it can do via `Driver.capabilities()`. A scenario can ask for an
action the chosen backend can't perform — e.g. a two-finger pinch on a single-touch backend. Before
this check, the only gate was `gestures.py`'s `_require_multi_touch`, fired mid-run, so a scenario
whose last step needed an unsupported capability ran every earlier step on a device first, then
failed late. `unsupported()` moves that check up front: it is a pure function of (scenario,
capability set), so the runner can fail a scenario *before* any device work, deterministically and
with one aggregated message (prime directive #2: fail fast and clearly).

Several gates below were written for idb, the one backend that lacked `multiTouch` and
`textSelection`. With idb retired (BE-0290), no current backend lacks either, so those gates no
longer reject any scenario in practice; they stay because the check is capability-driven, not
backend-specific, and a future backend may reintroduce the gap.

The map gates only the **true hard requirements** the capability set cleanly decides:

- `pinch` / `rotate` need `multiTouch`.
- `selectOption` needs `selectOption` (BE-0191): a web-only action that sets a native `<select>`;
  iOS / Android backends raise `UnsupportedAction`, so a scenario with this step is rejected before
  any device work on those platforms.
- `select` / `copy` need `textSelection` (BE-0280): select-all + clipboard copy on the focused
  field. A backend with no select-all handle raises `UnsupportedAction` and does not advertise the
  token, so a scenario selecting or copying is rejected up front. `delete` / `clear` are not gated:
  they actuate `delete_text` (a run of backspaces), which every backend backs.
- a `visual` assertion needs `screenshot`.
- a device-control step needs the capability token for its own operation (BE-0212 split the coarse
  `deviceControl` of BE-0128 into per-operation tokens): `setLocation` needs
  `deviceControl.setLocation`, the clipboard steps need `deviceControl.clipboard`, `push` needs
  `deviceControl.push`, and so on. A backend that backs only part of the family (the Android
  emulator: setLocation + clipboard) thus passes preflight for what it supports and fails fast for
  the rest, each unsupported step named individually. `relaunch` is not here: it is gated by the
  injected relauncher, not `DeviceControl`.
- a `permissions` entry needs the capability token for its own *service* (BE-0276), not one token
  for the field: `deviceControl.permissions.<service>`. Backends honor different subsets of the
  shared vocabulary (iOS has no TCC service for `notifications`), so each named service is gated —
  and, if unsupported, reported — individually, the same per-occurrence shape as device control.
- every run needs `query` + `elements` (the baseline read path).

Deliberately **not** gated (an audit of what each construct actually depends on, BE-0082):

- `conditionWait` — the orchestrator implements every wait by polling `query()` / the network
  collector (`orchestrator/waits.py`), so no backend needs the capability; gating it would reject
  scenarios that run fine.
- `network` — the iOS (XCUITest) and Android backends do not advertise `network` (that token means
  *native* observation, which Playwright has), yet they still capture traffic through the app-side
  collector, so a `request` / `event` / `requestSequence` / `responseSchema` assertion or
  `until: { request }` wait runs on them. Gating on the capability would wrongly reject those.
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


def _select_option_locations(sc: Scenario) -> list[str]:
    """The paths where a selectOption step appears."""
    return [path for path, step in _walk_steps(sc.steps) if step.select_option is not None]


def _text_selection_locations(sc: Scenario) -> list[str]:
    """The paths where a `select` or `copy` step appears (BE-0280).

    `delete` / `clear` are excluded: they actuate `delete_text`, which every backend backs, so they
    need no capability. Only select-all / copy depend on the `textSelection` capability.
    """
    return [
        path
        for path, step in _walk_steps(sc.steps)
        if step.select is not None or step.copy_ is not None
    ]


def _visual_locations(sc: Scenario) -> list[str]:
    """The paths where a visual assertion appears."""
    return [path for path, a in _assertions_with_path(sc) if a.visual is not None]


def _permission_service_locations(service: str) -> Callable[[Scenario], list[str]]:
    """One location (`scenario.permissions`) when `service` is named in the scenario's field."""
    return lambda sc: ["scenario.permissions"] if service in sc.permissions else []


# Each device-control operation, paired with the per-operation capability token it needs (BE-0212)
# and a predicate matching the step that triggers it. Clipboard read/write/clear share one token,
# as do background/foreground and the status-bar override/clear pair — operations that always ship
# together. A backend advertises exactly the tokens it can honor, so preflight gates each step on
# its own operation rather than the family as a whole. `relaunch` is not here: it is gated by the
# injected relauncher, not `DeviceControl`.
_DEVICE_CONTROL_OPS: tuple[tuple[str, str, Callable[[Step], bool]], ...] = (
    (base.Capability.DC_SET_LOCATION, "setLocation", lambda s: s.set_location is not None),
    (
        base.Capability.DC_CLIPBOARD,
        "clipboard step",
        lambda s: s.set_clipboard is not None or s.clear_clipboard is not None,
    ),
    (base.Capability.DC_PUSH, "push", lambda s: s.push is not None),
    (base.Capability.DC_CLEAR_KEYCHAIN, "clearKeychain", lambda s: s.clear_keychain is not None),
    (
        base.Capability.DC_APP_LIFECYCLE,
        "background / foreground",
        lambda s: s.background is not None or s.foreground is not None,
    ),
    (
        base.Capability.DC_STATUS_BAR,
        "status-bar override / clear",
        lambda s: s.override_status_bar is not None or s.clear_status_bar is not None,
    ),
)


def _step_locations(matches: Callable[[Step], bool]) -> Callable[[Scenario], list[str]]:
    """The paths of every step (recursing into `if` / `forEach`) that `matches`."""
    return lambda sc: [path for path, step in _walk_steps(sc.steps) if matches(step)]


# Capabilities every run needs regardless of which constructs it uses (the baseline read path).
_BASELINE = (base.Capability.QUERY, base.Capability.ELEMENTS)

_REQUIREMENTS = (
    _Requirement(
        base.Capability.MULTI_TOUCH,
        "pinch / rotate (two-finger gesture)",
        _multi_touch_locations,
    ),
    _Requirement(
        base.Capability.SELECT_OPTION,
        "selectOption (web <select> switch; not supported on iOS / Android)",
        _select_option_locations,
    ),
    _Requirement(
        base.Capability.TEXT_SELECTION,
        "select / copy (select-all + clipboard copy; not supported by this backend)",
        _text_selection_locations,
    ),
    _Requirement(
        base.Capability.SCREENSHOT,
        "visual assertion",
        _visual_locations,
    ),
    *(
        _Requirement(token, f"{label} (device control)", _step_locations(matches))
        for token, label, matches in _DEVICE_CONTROL_OPS
    ),
    # One requirement per vocabulary service (BE-0276), not per operation: `permissions` is gated
    # per service because backends honor different subsets of the shared vocabulary (iOS has no TCC
    # service for `notifications`), so an unsupported service is named individually rather than the
    # field as a whole. Built from the fixed vocabulary (`base.PERMISSION_SERVICES`), not the
    # scenario, exactly like every other requirement here — `locations` does the per-scenario work.
    *(
        _Requirement(
            base.permission_capability(service),
            f"permissions.{service} (device control)",
            _permission_service_locations(service),
        )
        for service in base.PERMISSION_SERVICES
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

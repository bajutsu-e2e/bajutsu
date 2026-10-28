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
- `setPickerValue` needs `pickerWheel` (BE-0356): a picker wheel is an iOS control, and only the
  resident-runner XCUITest backend can land on a named row deterministically, so a scenario setting
  a wheel's value is rejected up front on every other backend.
- `selectPhotos` needs `selectPhotos` (roadmap item): a `PHPickerViewController` grid is an iOS
  control, and only the resident-runner XCUITest backend can address it at all, so a scenario using
  this step is rejected up front on every other backend.
- `select` / `copy` need `textSelection` (BE-0280): select-all + clipboard copy on the focused
  field. A backend with no select-all handle raises `UnsupportedAction` and does not advertise the
  token, so a scenario selecting or copying is rejected up front. `delete` / `clear` are not gated:
  `delete` actuates `delete_text` (a run of backspaces), which every backend backs, and `clear` only
  *opportunistically* uses `textSelection` (select-all then one backspace) when a backend advertises
  it, falling back to a counted `delete_text` run when it doesn't — so neither ever needs the token.
- a `visual` assertion needs `screenshot`.
- a device-control step needs the capability token for its own operation (BE-0212 split the coarse
  `deviceControl` of BE-0128 into per-operation tokens): `setLocation` needs
  `deviceControl.setLocation`, the clipboard steps need `deviceControl.clipboard`, `push` needs
  `deviceControl.push`, and so on. A backend that backs only part of the family (the Android
  emulator: setLocation + clipboard) thus passes preflight for what it supports and fails fast for
  the rest, each unsupported step named individually. `relaunch` is not here: it is gated by the
  injected relauncher, not `DeviceControl`.
- a `permissions` entry is not gated here: it is a setting applied before the steps, not a step, so
  the BE-0467 settings check (`settings_support.py`) judges each service against the same BE-0276
  token, and honours a target's `run.ignoredSettings` declaration in one place.
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

from collections.abc import Callable, Iterator, Sequence, Set
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bajutsu.common.drivers import base
from bajutsu.common.scenario import Assertion, Scenario, Step

if TYPE_CHECKING:
    from bajutsu.common.capability.worker_capability import WorkerCapability


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
        # `web.steps` and `app.steps` are deliberately not recursed into. A `web:`/`app:` step
        # itself is still walked and gated (its own `_Requirement` fires wherever it sits), but
        # what happens *inside* the block is not separately visible here — a construct needing a
        # capability neither backend today declares only alongside WEBVIEW/APP_CONTEXT does not
        # currently exist, so this is a real but so-far harmless gap, not fixed here (this item
        # keeps it rather than fixing `web:`'s pre-existing one as a drive-by, which is out of its
        # lane). `navigate` (BE-0464) is now such a construct; `_navigate_locations` follows `web:`
        # / `app:` blocks for it alone.


def _walk_scenario(sc: Scenario) -> Iterator[tuple[str, Step]]:
    """Every step the run will execute, across all three phases (BE-0392).

    The lifecycle phases take the full step grammar, so a construct in one needs the same capability
    gate as one in `steps`. Missed here, an unsupported teardown step passes the preflight, a device
    is leased, the whole scenario is driven, and only then does the step fail — and a failing `cleanup`
    rule on an otherwise-passing run *becomes* that run's failure. Late failure after full device
    work is exactly what this module exists to prevent.
    """
    yield from _walk_steps(sc.setup, "setup ")
    yield from _walk_steps(sc.steps)
    for i, rule in enumerate(sc.cleanup):
        yield from _walk_steps(rule.steps, f"cleanup[{i}] ")


def _assertions_with_path(scenario: Scenario) -> Iterator[tuple[str, Assertion]]:
    """Every assertion with its path across the whole step tree.

    Covers the scenario `expect`, each step's `assert`, and every `if` condition — in the lifecycle
    phases as well as in `steps`.
    """
    for i, a in enumerate(scenario.expect or []):
        yield f"expect[{i}]", a
    for step_path, step in _walk_scenario(scenario):
        for j, a in enumerate(step.assert_ or []):
            yield f"{step_path} > assert[{j}]", a
        if step.if_ is not None:
            yield f"{step_path} > if > condition", step.if_.condition


def _multi_touch_locations(sc: Scenario) -> list[str]:
    """The paths where a two-finger gesture (pinch/rotate) appears."""
    return [
        path
        for path, step in _walk_scenario(sc)
        if step.pinch is not None or step.rotate is not None
    ]


def _select_option_locations(sc: Scenario) -> list[str]:
    """The paths where a selectOption step appears."""
    return [path for path, step in _walk_scenario(sc) if step.select_option is not None]


def _navigate_locations(sc: Scenario) -> list[str]:
    """The paths where a navigate step appears, inside a `web:` / `app:` block included (BE-0464).

    `_walk_steps` skips those blocks on purpose, but no backend that runs one (the iOS WebView
    context, the cross-app context) can open a URL, so a `navigate` nested there would otherwise reach
    a leased device before failing. Recursing for this one construct closes that gap without moving
    every other construct's failure from run time to preflight.
    """

    def found(pairs: Iterator[tuple[str, Step]]) -> Iterator[str]:
        for path, step in pairs:
            if step.navigate is not None:
                yield path
            for name, block in (("web", step.web), ("app", step.app)):
                if block is not None:
                    yield from found(_walk_steps(block.steps, f"{path} > {name}"))

    paths = list(found(_walk_scenario(sc)))
    # `_walk_scenario` stops at setup / steps / cleanup; an interrupt's recovery steps run on the
    # leased device too, so a `navigate` there has to be caught before the lease as well.
    for i, entry in enumerate(sc.interrupts):
        paths.extend(found(_walk_steps(entry.steps, f"interrupts[{i}] ")))
    return paths


def _text_selection_locations(sc: Scenario) -> list[str]:
    """The paths where a `select` or `copy` step appears (BE-0280).

    `delete` / `clear` are excluded: they actuate `delete_text`, which every backend backs, so they
    need no capability. Only select-all / copy depend on the `textSelection` capability.
    """
    return [
        path
        for path, step in _walk_scenario(sc)
        if step.select is not None or step.copy_ is not None
    ]


def _visual_locations(sc: Scenario) -> list[str]:
    """The paths where a visual assertion appears."""
    return [path for path, a in _assertions_with_path(sc) if a.visual is not None]


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
    (base.Capability.DC_BACKGROUND, "background", lambda s: s.background is not None),
    (base.Capability.DC_FOREGROUND, "foreground", lambda s: s.foreground is not None),
    (
        base.Capability.DC_STATUS_BAR,
        "status-bar override / clear",
        lambda s: s.override_status_bar is not None or s.clear_status_bar is not None,
    ),
)


def _step_locations(matches: Callable[[Step], bool]) -> Callable[[Scenario], list[str]]:
    """The paths of every step (recursing into `if` / `forEach`) that `matches`."""
    return lambda sc: [path for path, step in _walk_scenario(sc) if matches(step)]


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
        base.Capability.URL_NAVIGATION,
        "navigate (open a URL; web only, not supported on iOS / Android)",
        _navigate_locations,
    ),
    _Requirement(
        base.Capability.PICKER_WHEEL,
        "setPickerValue (iOS picker-wheel value; iOS XCUITest only)",
        _step_locations(lambda s: s.set_picker_value is not None),
    ),
    _Requirement(
        base.Capability.SELECT_PHOTOS,
        "selectPhotos (iOS photo-picker selection; iOS XCUITest only)",
        _step_locations(lambda s: s.select_photos is not None),
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
    _Requirement(
        base.Capability.HANDLE_SYSTEM_ALERT,
        "handleSystemAlert (iOS system-alert tap; iOS XCUITest only)",
        _step_locations(lambda s: s.handle_system_alert is not None),
    ),
    _Requirement(
        base.Capability.APP_CONTEXT,
        "app (cross-app UI control; iOS XCUITest only)",
        _step_locations(lambda s: s.app is not None),
    ),
    *(
        _Requirement(token, f"{label} (device control)", _step_locations(matches))
        for token, label, matches in _DEVICE_CONTROL_OPS
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


# --- The worker capability check (BE-0450) -------------------------------------------------------
#
# `unsupported()` answers "can this driver perform this step?"; the functions below answer "can this
# worker run this driver at all, and with how many targets?" from the worker's `worker.yaml`, the
# driver's host requirement, and the machine's host. They stay pure — no device, no clock — so the
# check sits on the deterministic path (prime directive 1) and needs no Simulator to test.

WORKER_CAPABILITY_PREFIX = "worker-capability-unsupported:"

_HOST_NAMES = {"darwin": "macOS", "linux": "Linux", "windows": "Windows"}


def actuator_unavailable_reason(
    actuator: str,
    worker: WorkerCapability,
    host: str,
    host_os_for: Callable[[str], frozenset[str] | None] | None = None,
) -> str | None:
    """Why *worker* on *host* cannot run *actuator*'s driver, or None when it can (rules 1 and 2).

    Rule 1, the host requirement, applies only to a driver that runs on this host: on a device cloud
    the driver runs on the provider's host, and on an `appium` grid its platform's driver runs on the
    grid's (BE-0452).

    Args:
        actuator: The actuator (driver) name.
        worker: The worker capability in force.
        host: The machine's host operating system (`backends.current_host_os()`).
        host_os_for: The driver host-requirement lookup; `backends.host_os_for` when None.
    """
    if worker.drives_on_host(actuator):
        if host_os_for is None:
            from bajutsu.common.backends import host_os_for as default_host_os_for

            host_os_for = default_host_os_for
        hosts = host_os_for(actuator)
        if hosts is not None and host not in hosts:
            names = " or ".join(_HOST_NAMES.get(h, h) for h in sorted(hosts))
            return f"{actuator} runs only on {names}; this worker runs on {host}"
    if actuator not in worker.drivers:
        return (
            f"{actuator} is not among this worker's drivers ({', '.join(worker.drivers) or 'none'})"
        )
    return None


def filter_actuators(
    actuators: Sequence[str],
    worker: WorkerCapability,
    host: str,
    host_os_for: Callable[[str], frozenset[str] | None] | None = None,
) -> tuple[list[str], list[str]]:
    """Split *actuators* into the ones *worker* can run and the reasons for the ones it cannot.

    An actuator with no driver yet is kept untouched: the worker file cannot name it, and
    `select_actuator` already reports it as planned rather than available.

    Returns:
        The runnable actuators in their original order, and one reason per dropped actuator.
    """
    from bajutsu.common.backends import IMPLEMENTED

    kept: list[str] = []
    reasons: list[str] = []
    for actuator in actuators:
        reason = (
            actuator_unavailable_reason(actuator, worker, host, host_os_for)
            if actuator in IMPLEMENTED
            else None
        )
        if reason is None:
            kept.append(actuator)
        else:
            reasons.append(reason)
    return kept, reasons


def scenario_target_count(scenario: Scenario) -> int:
    """How many targets — and so devices — *scenario* holds at once, summed over every pool."""
    return len(scenario.target_names) or 1


def worker_capability_unsupported(
    scenario: Scenario,
    backends_for: Callable[[str | None], Sequence[str]],
    worker: WorkerCapability,
    host: str,
    host_os_for: Callable[[str], frozenset[str] | None] | None = None,
) -> list[str]:
    """The reasons *worker* on *host* cannot run *scenario*, empty when it can (BE-0450).

    Rules 1 and 2 run once per target the scenario needs: a target fails when none of its
    requested actuators survives the host and `drivers` filter, so a fallback list such as
    `[ios, web]` still passes on Linux. Rule 3 runs once per scenario against `maxTargetsPerJob`.

    Args:
        scenario: The scenario to judge.
        backends_for: The requested backend list (platform tokens or actuator names) for one of
            the scenario's declared targets, or for the run's single resolved target when called
            with None — the effective target configuration with any `--backend` override applied.
        worker: The worker capability in force.
        host: The machine's host operating system.
        host_os_for: The driver host-requirement lookup; `backends.host_os_for` when None.

    Returns:
        One reason per unmet requirement, each naming the driver or the limit; format them for a
        report with `worker_capability_failure`.
    """
    from bajutsu.common.backends import resolve_actuators

    reasons: list[str] = []
    targets: list[str | None] = [*scenario.target_names] or [None]
    for target in targets:
        kept, dropped = filter_actuators(
            resolve_actuators(list(backends_for(target))), worker, host, host_os_for
        )
        if not kept and dropped:
            where = f"target '{target}': " if target is not None else ""
            reasons.append(where + "; ".join(dropped))
    count = scenario_target_count(scenario)
    if worker.max_targets_per_job is not None and count > worker.max_targets_per_job:
        reasons.append(
            f"the scenario declares {count} targets; this worker allows at most "
            f"{worker.max_targets_per_job} per job (maxTargetsPerJob)"
        )
    return reasons


def worker_capability_failure(reasons: Sequence[str]) -> str:
    """The one-line preflight failure for *reasons*, under the fixed prefix tests and readers key on."""
    return f"{WORKER_CAPABILITY_PREFIX} {'; '.join(reasons)}"

"""Action dispatch core: the handler registry, the action-kind lookup, and progress labels.

One-shot action handlers live in `handlers/<group>.py` and self-register via `@_handler(kind)`;
this module owns the registry they fill and the `_do_action` dispatcher that runs them. `wait`
and `assert` are conditions handled by the run loop, not here.
"""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.drivers import base
from bajutsu.orchestrator.types import DeviceControl, RelaunchFn
from bajutsu.scenario import STEP_ACTIONS, Step

# The actions the run loop can see, derived from the scenario model (STEP_ACTIONS) minus the
# compile-time-only `use` macro, which is expanded away before the run. Deriving it means a new
# action shows up here automatically — it is declared once, on the Step model.
_RUNTIME_ACTIONS = tuple(a for a in STEP_ACTIONS if a != "use")


def _action_of(step: Step) -> str:
    for a in _RUNTIME_ACTIONS:
        if getattr(step, a) is not None:
            return a
    raise AssertionError("no valid action on step (guaranteed by scenario validation)")


def _selector_hint(obj: object) -> str:
    """A short target string for a progress label — the first id/label found on an action object
    or its nested selector (e.g. `type`'s `into`, `swipe`'s `on`). Empty when nothing identifies
    it. Never returns typed text (kept out of progress so secrets don't leak)."""
    for attr in ("id", "label", "id_matches", "label_matches"):
        v = getattr(obj, attr, None)
        if v:
            return str(v)
    for attr in ("into", "on", "sel", "of", "within"):
        nested = getattr(obj, attr, None)
        if nested is not None:
            hint = _selector_hint(nested)
            if hint:
                return hint
    return ""


def _step_label(step: Step, kind: str) -> str:
    """A concise description of a step for progress output: the step's own `name` if set,
    otherwise the action kind plus its target id/label (e.g. "tap home.title")."""
    if step.name:
        return step.name
    hint = _selector_hint(getattr(step, kind))
    pretty = kind.rstrip("_").replace("_", " ")
    return f"{pretty} {hint}".strip()


# One-shot action handlers, keyed by action kind, filled by the `@_handler(kind)` decorator.
ActionHandler = Callable[
    [base.Driver, Step, "RelaunchFn | None", "DeviceControl | None", "dict[str, str] | None"],
    None,
]
_HANDLERS: dict[str, ActionHandler] = {}


def _handler(kind: str) -> Callable[[ActionHandler], ActionHandler]:
    def register(fn: ActionHandler) -> ActionHandler:
        _HANDLERS[kind] = fn
        return fn

    return register


def _need_control(control: DeviceControl | None, name: str) -> DeviceControl:
    """Return the device control, or fail clearly if this run has none (e.g. the fake driver)."""
    if control is None:
        raise base.UnsupportedAction(
            f"{name} requires a real device environment (not supported on fake driver)"
        )
    return control


def _do_action(
    driver: base.Driver,
    step: Step,
    relaunch: RelaunchFn | None = None,
    control: DeviceControl | None = None,
    bindings: dict[str, str] | None = None,
) -> None:
    """Run a one-shot action (tap / longPress / type / swipe / relaunch / device control / http)
    by dispatching to its registered handler. `wait` and `assert` live in the run loop."""
    handler = _HANDLERS.get(_action_of(step))
    if handler is None:
        raise AssertionError("unhandled action")
    handler(driver, step, relaunch, control, bindings)

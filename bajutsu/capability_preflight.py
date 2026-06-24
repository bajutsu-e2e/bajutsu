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


# A construct the capability set can gate: the capability it needs, a human label, and a predicate
# that detects whether the scenario uses it. Listing them here is the single place the map lives.
@dataclass(frozen=True)
class _Requirement:
    capability: str
    label: str
    used_by: Callable[[Scenario], bool]


def _walk_steps(steps: list[Step]) -> Iterator[Step]:
    """Every step, recursing into the nested blocks of `if` / `forEach`. Those run at runtime (only
    `use` is expanded away before the run), so a construct nested inside them must still be seen —
    otherwise the preflight misses it and the run fails late, the very thing it exists to prevent."""
    for step in steps:
        yield step
        if step.if_ is not None:
            yield from _walk_steps(step.if_.then)
            yield from _walk_steps(step.if_.else_ or [])
        if step.for_each is not None:
            yield from _walk_steps(step.for_each.steps)


def _assertions(scenario: Scenario) -> Iterator[Assertion]:
    """Every assertion the scenario evaluates — the scenario `expect`, each step's `assert`, and an
    `if` condition — across the whole step tree."""
    yield from scenario.expect or []
    for step in _walk_steps(scenario.steps):
        yield from step.assert_ or []
        if step.if_ is not None:
            yield step.if_.condition


# Capabilities every run needs regardless of which constructs it uses (the baseline read path).
_BASELINE = (base.Capability.QUERY, base.Capability.ELEMENTS)

_REQUIREMENTS = (
    _Requirement(
        base.Capability.MULTI_TOUCH,
        "pinch / rotate (two-finger gesture)",
        lambda sc: any(
            st.pinch is not None or st.rotate is not None for st in _walk_steps(sc.steps)
        ),
    ),
    _Requirement(
        base.Capability.SCREENSHOT,
        "visual assertion",
        lambda sc: any(a.visual is not None for a in _assertions(sc)),
    ),
)


def unsupported(scenario: Scenario, capabilities: Set[str]) -> list[str]:
    """The reasons `scenario` can't run on a backend with `capabilities` — one per unsupported
    construct, empty when it is runnable. Pure: no device, no clock, no network. `capabilities` is
    any set type (the runner passes the driver's frozen `CAPABILITIES` directly)."""
    reasons = [f"running needs '{cap}'" for cap in _BASELINE if cap not in capabilities]
    reasons += [
        f"{req.label} needs '{req.capability}'"
        for req in _REQUIREMENTS
        if req.used_by(scenario) and req.capability not in capabilities
    ]
    return reasons

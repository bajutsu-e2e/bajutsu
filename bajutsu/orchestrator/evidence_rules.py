"""capturePolicy firing and `extract` — deciding which evidence a step records, and pulling
element properties into runtime vars.*."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator, Mapping

from bajutsu.drivers import base
from bajutsu.scenario import CaptureRule, Extract, Scenario, Selector, Step

# Always captured, regardless of capturePolicy: an after-screenshot and the element
# tree per step (instant); interval recordings for the whole scenario live in the run loop.
_BASELINE_INSTANT = ("screenshot.after", "elements")

# Scenario-wide interval recordings, in canonical order. These are heavy, so they are opt-in
# (BE-0028): recorded only when a scenario actually requests the kind (see requested_intervals).
_SCENARIO_INTERVALS = ("video", "deviceLog", "appTrace")

_DSL_ACTION = {
    "long_press": "longPress",
    "double_tap": "doubleTap",
    "assert_": "assert",
    "handle_system_alert": "handleSystemAlert",
}


def _primary_selector(step: Step) -> Selector | None:
    if step.tap is not None:
        return step.tap
    if step.double_tap is not None:
        return step.double_tap
    if step.long_press is not None:
        return step.long_press.sel
    if step.type is not None:
        return step.type.into
    if step.swipe is not None:
        return step.swipe.on
    if step.pinch is not None:
        return step.pinch.sel
    if step.rotate is not None:
        return step.rotate.sel
    if step.handle_system_alert is not None:
        return step.handle_system_alert.sel
    return None


def _rule_fires(
    rule: CaptureRule, kind: str, primary_id: str | None, screen_changed: bool, ok: bool
) -> bool:
    trigger = rule.on
    if trigger.action is not None:
        if trigger.action != _DSL_ACTION.get(kind, kind):
            return False
        if trigger.id_matches is not None:
            return primary_id is not None and fnmatch.fnmatchcase(primary_id, trigger.id_matches)
        return True
    if trigger.event == "screenChanged":
        return screen_changed
    if trigger.result == "error":
        return not ok
    return False


def _dedupe(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _kind_of(token: str) -> str:
    return token.partition(".")[0]


def _extract_stable_key(
    elements: list[base.Element], extracts: Mapping[str, Extract]
) -> tuple[object, ...]:
    """A settle projection for `extract`: whole-screen layout plus each target's read property.

    Two halves. The **layout** half is the driver-side settle projection (`_stable_key` in
    `coordinate_tree.py`): every element's identifier and frame, sorted — a whole-screen resolve
    stability check that a text-only animation (which does not move layout) leaves quiet. The
    **target** half carries, per extract, only the property that extract copies out (`ext.prop`) on
    the element its selector resolves to — scoped to the target, so an `extract` polls until *the
    value it actually reads* stops changing, not until every live-updating label elsewhere on the
    screen (a timer, a "Loading…" animation) happens to be quiet, which would burn the whole deadline
    on every extract step on such a screen (BE-0299 Unit 3).

    When a target does not resolve to exactly one element — a screen still mid-transition — the target
    half falls back to the whole-screen projection of that property, so the settle keeps polling until
    the selector resolves uniquely rather than reading a half-rendered screen. Both halves are sorted
    and every value is coerced with `or ""`, so the key is a function of the element *set* (not the
    order the driver returned it in) and an optional field reported as `None` on one read and `""` on
    the next does not look changed; a genuine `None → real value` change still differs.
    """
    layout = tuple(sorted((e["identifier"] or "", e["frame"]) for e in elements))
    targets: list[tuple[str, tuple[str, ...]]] = []
    for name, ext in sorted(extracts.items()):
        matched = base.find_all(elements, ext.sel.as_selector())
        projected: tuple[str, ...]
        if len(matched) == 1:
            projected = (matched[0].get(ext.prop) or "",)
        else:  # not uniquely resolvable yet: keep polling on the whole-screen prop until it is
            projected = tuple(sorted((e.get(ext.prop) or "") for e in elements))
        targets.append((name, projected))
    return (layout, tuple(targets))


def _run_extract(
    elements: list[base.Element],
    extracts: Mapping[str, Extract],
    live_bindings: dict[str, str],
) -> tuple[bool, str]:
    """Resolve each extract selector and store the property value in live_bindings."""
    for name, ext in extracts.items():
        try:
            el = base.resolve_unique(elements, ext.sel.as_selector())
        except base.SelectorError as e:
            return False, f"extract '{name}': {e}"
        raw: str | None = el.get(ext.prop)
        if raw is None:
            return False, f"extract '{name}': {ext.prop} is None on the matched element"
        live_bindings[f"vars.{name}"] = str(raw)
    return True, ""


def _all_steps(steps: list[Step]) -> Iterator[Step]:
    """Every step, descending into the nested bodies of `if` / `forEach` so a nested step's
    inline `capture` is seen too."""
    for step in steps:
        yield step
        if step.if_ is not None:
            yield from _all_steps(step.if_.then)
            if step.if_.else_ is not None:
                yield from _all_steps(step.if_.else_)
        if step.for_each is not None:
            yield from _all_steps(step.for_each.steps)


def requested_intervals(scenario: Scenario) -> list[str]:
    """The scenario-wide interval kinds (video / deviceLog / appTrace) the scenario actually
    asks for — via a `capturePolicy` rule or any step's inline `capture`, nested steps included.
    Empty by default, so a scenario that requests no heavy capture records none (BE-0028)."""
    requested = {_kind_of(token) for rule in scenario.capture_policy for token in rule.capture}
    requested.update(
        _kind_of(token) for step in _all_steps(scenario.steps) for token in (step.capture or [])
    )
    return [kind for kind in _SCENARIO_INTERVALS if kind in requested]


def _collect_captures(
    scenario: Scenario, step: Step, kind: str, ok: bool, screen_changed: bool
) -> list[str]:
    """Capture kinds for this step: the always-on instant baseline, plus inline
    `capture` and any matching capturePolicy rules."""
    fired: list[str] = [*_BASELINE_INSTANT, *(step.capture or [])]
    primary = _primary_selector(step)
    primary_id = primary.first_id() if primary is not None else None
    for rule in scenario.capture_policy:
        if _rule_fires(rule, kind, primary_id, screen_changed, ok):
            fired.extend(rule.capture)
    return _dedupe(fired)

"""capturePolicy firing and `extract` — deciding which evidence a step records, and pulling
element properties into runtime vars.*."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping

from bajutsu.drivers import base
from bajutsu.scenario import CaptureRule, Extract, Scenario, Selector, Step

# Always captured, regardless of capturePolicy: an after-screenshot and the element
# tree per step (instant); interval recordings for the whole scenario live in the run loop.
_BASELINE_INSTANT = ("screenshot.after", "elements")

_DSL_ACTION = {"long_press": "longPress", "double_tap": "doubleTap", "assert_": "assert"}


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


def _collect_captures(
    scenario: Scenario, step: Step, kind: str, ok: bool, screen_changed: bool
) -> list[str]:
    """Capture kinds for this step: the always-on instant baseline, plus inline
    `capture` and any matching capturePolicy rules."""
    fired: list[str] = [*_BASELINE_INSTANT, *(step.capture or [])]
    primary = _primary_selector(step)
    primary_id = primary.id if primary is not None else None
    for rule in scenario.capture_policy:
        if _rule_fires(rule, kind, primary_id, screen_changed, ok):
            fired.extend(rule.capture)
    return _dedupe(fired)

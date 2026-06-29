"""Action-capture record — proxy-actuation capture of tap / type / swipe (BE-0012).

Pure core: hit-test a point against the element tree, resolve a stable selector via the
id → label(+index) ladder, and emit scenario steps. No driver, no HTTP, no model call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from bajutsu import doctor
from bajutsu.drivers import base
from bajutsu.redaction import Redactor
from bajutsu.scenario.models import Selector, Step, Swipe, TypeText


@dataclass(frozen=True)
class CaptureResult:
    """The outcome of resolving a capture point against the element tree."""

    element: base.Element
    selector: Selector
    rung: str
    score: doctor.Score
    ambiguity: list[base.Element] | None = None
    refused: str | None = None


_REFUSED_SENTINEL: base.Element = {
    "identifier": None,
    "label": None,
    "traits": [],
    "value": None,
    "frame": (0, 0, 0, 0),
}
_REFUSED_SELECTOR = Selector(id="__refused__")
_REFUSED_SCORE = doctor.Score(
    actionable=0,
    with_id=0,
    id_coverage=0.0,
    namespace_conformance=0.0,
    duplicate_ids=0,
    grade="Blocked",
    no_actionable=True,
    missing_id=[],
    off_namespace=[],
    duplicates=[],
)


def _refused(reason: str) -> CaptureResult:
    return CaptureResult(
        element=_REFUSED_SENTINEL,
        selector=_REFUSED_SELECTOR,
        rung="none",
        score=_REFUSED_SCORE,
        refused=reason,
    )


def hit_test(elements: list[base.Element], point: tuple[float, float]) -> base.Element | None:
    """Find the smallest actionable element whose frame contains *point*."""
    px, py = point
    inner: base.Frame = (px, py, 0.0, 0.0)
    best: base.Element | None = None
    best_area = float("inf")
    for el in elements:
        if not doctor._is_actionable(el):
            continue
        if not base._contains(el["frame"], inner):
            continue
        _, _, w, h = el["frame"]
        area = w * h
        if area < best_area:
            best = el
            best_area = area
    return best


def selector_for_element(el: base.Element, elements: list[base.Element]) -> Selector | None:
    """Stable selector for *el*: id → label(+index) → None ("faithful or nothing")."""
    ident = el["identifier"]
    if ident:
        return Selector(id=ident)
    label = el["label"]
    if label:
        same_label = [e for e in elements if e["label"] == label]
        if len(same_label) > 1:
            idx = next(i for i, e in enumerate(same_label) if e is el)
            return Selector(label=label, index=idx)
        return Selector(label=label)
    return None


def resolve_capture(
    elements: list[base.Element],
    point: tuple[float, float],
    id_namespaces: list[str],
) -> CaptureResult:
    """Resolve a capture point to a selector, or refuse with a reason."""
    el = hit_test(elements, point)
    if el is None:
        return _refused("no actionable element at this point")

    sel = selector_for_element(el, elements)
    if sel is None:
        return _refused("element needs an accessibilityIdentifier (no id and no label)")

    rung = "id" if sel.id else ("label+index" if sel.index is not None else "label")
    raw = sel.as_selector()

    try:
        base.resolve_unique(elements, raw)
    except base.AmbiguousSelector:
        ambiguous = base.find_all(elements, raw)
        return CaptureResult(
            element=el,
            selector=sel,
            rung=rung,
            score=doctor.score(elements, id_namespaces),
            ambiguity=ambiguous,
        )
    except base.ElementNotFound:
        return _refused("element not found after resolution")

    return CaptureResult(
        element=el,
        selector=sel,
        rung=rung,
        score=doctor.score(elements, id_namespaces),
    )


def step_for_tap(sel: Selector) -> Step:
    """Emit a tap step."""
    return Step(tap=sel)


def step_for_type(sel: Selector, text: str, redactor: Redactor | None = None) -> Step:
    """Emit a type step, masking the text when a redactor is active."""
    masked = redactor.redact_text(text) if redactor and redactor.active else text
    return Step(type=TypeText(text=masked, into=sel))


def step_for_swipe(
    from_norm: tuple[float, float],
    to_norm: tuple[float, float],
    elements: list[base.Element] | None = None,
    screen_size: tuple[float, float] | None = None,
) -> Step:
    """Emit a swipe step from two normalized [0,1] points.

    When *elements* and *screen_size* are given and both endpoints resolve to the same
    single element, upgrade to the stabler ``Swipe(on=sel, direction=…)`` form.
    """
    if elements is not None and screen_size is not None:
        sw, sh = screen_size
        from_el = hit_test(elements, (from_norm[0] * sw, from_norm[1] * sh))
        to_el = hit_test(elements, (to_norm[0] * sw, to_norm[1] * sh))
        if from_el is not None and to_el is not None and from_el is to_el:
            sel = selector_for_element(from_el, elements)
            if sel is not None:
                return Step(swipe=Swipe(on=sel, direction=_swipe_direction(from_norm, to_norm)))
    return Step.model_validate({"swipe": {"from": from_norm, "to": to_norm}})


def _swipe_direction(
    from_pt: tuple[float, float], to_pt: tuple[float, float]
) -> Literal["up", "down", "left", "right"]:
    """Infer swipe direction from two points."""
    dx = to_pt[0] - from_pt[0]
    dy = to_pt[1] - from_pt[1]
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def screen_size_from_elements(elements: list[base.Element]) -> tuple[float, float]:
    """Max frame width/height across all elements (the screen bounds)."""
    w = max((el["frame"][0] + el["frame"][2] for el in elements), default=0.0)
    h = max((el["frame"][1] + el["frame"][3] for el in elements), default=0.0)
    return (w, h)

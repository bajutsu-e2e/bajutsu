"""Golden element-tree comparison for BE-0006.

Compares a recorded golden (expected normalized Element dicts) against a live
query() result, field by field: exact on identity/state fields, set-equal on
traits, and tolerant (sanity only) on frame geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bajutsu.drivers.base import Driver, Element, Frame, Selector, _contains

_ELEMENT_FIELDS = frozenset(Element.__required_keys__ | Element.__optional_keys__)


@dataclass(frozen=True)
class FieldMismatch:
    """One field of one control that differs between golden and actual."""

    control_id: str
    field: str
    expected: object
    actual: object

    def __str__(self) -> str:
        return f"`{self.control_id}`: {self.field} expected {self.expected!r} got {self.actual!r}"


@dataclass
class GoldenResult:
    """Aggregate result of comparing a full golden against a query() snapshot."""

    mismatches: list[FieldMismatch] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    frame_failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.missing and not self.frame_failures


def compare_element(expected: Element, actual: Element) -> list[FieldMismatch]:
    """Compare two Elements field by field per BE-0006 rules.

    Returns:
        A list of mismatches (empty when the elements match).
    """
    control_id = expected["identifier"] or "<unknown>"
    mismatches: list[FieldMismatch] = []

    for key in ("identifier", "label", "value"):
        exp_val = expected[key]
        act_val = actual[key]
        if exp_val != act_val:
            mismatches.append(FieldMismatch(control_id, key, exp_val, act_val))

    if set(expected["traits"]) != set(actual["traits"]):
        mismatches.append(
            FieldMismatch(
                control_id,
                "traits",
                tuple(sorted(expected["traits"])),
                tuple(sorted(actual["traits"])),
            )
        )

    return mismatches


def frame_is_sane(frame: Frame, screen: Frame) -> bool:
    """Check that a frame has non-zero dimensions and sits within screen bounds."""
    _x, _y, w, h = frame
    if w <= 0 or h <= 0:
        return False
    return _contains(screen, frame)


def compare_golden(
    golden: dict[str, Element],
    actual: list[Element],
    screen: Frame,
) -> GoldenResult:
    """Compare a golden dict (keyed by identifier) against query() results.

    Args:
        golden: Expected elements keyed by identifier.
        actual: The live query() snapshot.
        screen: Screen bounds for frame sanity checks.
    """
    actual_by_id: dict[str | None, Element] = {el["identifier"]: el for el in actual}
    result = GoldenResult()

    for control_id, expected_el in golden.items():
        actual_el = actual_by_id.get(control_id)
        if actual_el is None:
            result.missing.append(control_id)
            continue

        result.mismatches.extend(compare_element(expected_el, actual_el))

        if not frame_is_sane(actual_el["frame"], screen):
            result.frame_failures.append(control_id)

    return result


# ---------------------------------------------------------------------------
# Persistence — load / save golden JSON files
# ---------------------------------------------------------------------------


def _validate_element(data: dict[str, Any], control_id: str) -> Element:
    missing = _ELEMENT_FIELDS - set(data.keys())
    if missing:
        raise ValueError(
            f"golden entry '{control_id}' missing field(s): {', '.join(sorted(missing))}"
        )
    frame = data["frame"]
    if not isinstance(frame, list) or len(frame) != 4:
        raise ValueError(
            f"golden entry '{control_id}' frame must be a 4-element list, got {frame!r}"
        )
    embedded_id = data["identifier"]
    if embedded_id != control_id:
        raise ValueError(
            f"golden key '{control_id}' does not match embedded identifier {embedded_id!r}"
        )
    return Element(
        identifier=embedded_id,
        label=data["label"],
        traits=data["traits"],
        value=data["value"],
        frame=(frame[0], frame[1], frame[2], frame[3]),
    )


def load_golden(path: Path) -> dict[str, Element]:
    """Load a golden JSON file (identifier-keyed Element dicts)."""
    raw: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return {cid: _validate_element(el, cid) for cid, el in raw.items()}


def save_golden(elements: list[Element], ids: list[str], path: Path) -> None:
    """Save selected elements from a query() result as a golden file.

    Args:
        elements: The full query() snapshot.
        ids: Identifiers of the controls to pin.
        path: Destination JSON file.
    """
    by_id = {el["identifier"]: el for el in elements}
    golden: dict[str, Element] = {cid: by_id[cid] for cid in ids if cid in by_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(golden, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# End-to-end assertion — ties the full flow together
# ---------------------------------------------------------------------------


def assert_golden_tree(
    driver: Driver,
    golden_path: Path,
    anchor: Selector,
    screen: Frame,
    *,
    timeout: float = 10.0,
) -> GoldenResult:
    """Drive-wait-query-compare: the full golden assertion flow.

    Args:
        driver: The backend driver (already launched on the target screen).
        golden_path: Path to the golden JSON file.
        anchor: A selector for a known element on the target screen (waited on
            to confirm the screen has settled).
        screen: Screen bounds for frame sanity checks.
        timeout: Seconds to wait for the anchor element.

    Returns:
        The comparison result.

    Raises:
        TimeoutError: The anchor element did not appear within *timeout*.
    """
    if not driver.wait_for(anchor, timeout):
        raise TimeoutError(f"anchor {anchor!r} did not appear within {timeout}s")
    elements = driver.query()
    golden = load_golden(golden_path)
    return compare_golden(golden, elements, screen)

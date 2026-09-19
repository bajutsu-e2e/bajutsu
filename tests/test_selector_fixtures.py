"""Replay BE-0408's language-neutral selector fixtures against the Python reference implementation.

`tests/fixtures/be0408/` is the language-neutral contract a future Swift (BE-0409) and Kotlin
(BE-0410) selector resolver must agree with. This module is what keeps that contract honest: it
never lets the fixtures drift from `find_all` / `resolve_unique` (and, for the Android-only label
rule, `parse_hierarchy`) without failing here first — on the fast Linux gate, no device needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from bajutsu.common.drivers.adb import parse_hierarchy
from bajutsu.common.drivers.base import (
    AmbiguousSelector,
    Element,
    ElementNotFound,
    Selector,
    find_all,
    resolve_unique,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "be0408"
_SUPPORTED_SCHEMA = 1
# `resolve_unique`'s own out-of-range wording (base/_functions.py); the fixtures record only the
# noMatch/outOfRange classification a port must reproduce, never this implementation-specific text
# (see docs/selectors.md's porting contract) — so this marker exists only to tell the two failure
# shapes apart here, not as part of the contract itself.
_OUT_OF_RANGE_MARKER = "out of range"


def _load(name: str) -> dict[str, Any]:
    data = json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))
    if data["schema"] != _SUPPORTED_SCHEMA:
        raise ValueError(f"{name}: unsupported fixture schema {data['schema']!r}")
    return cast(dict[str, Any], data)


def _element(raw: dict[str, Any]) -> Element:
    frame = raw["frame"]
    return {
        "identifier": raw["identifier"],
        "label": raw["label"],
        "traits": raw["traits"],
        "value": raw["value"],
        "frame": (frame[0], frame[1], frame[2], frame[3]),
        "nativeZ": None,
    }


_SELECTOR_CASES = _load("selector_resolution.json")["cases"]
_DERIVED_LABEL_CASES = _load("android_derived_label.json")["cases"]


@pytest.mark.parametrize("case", _SELECTOR_CASES, ids=[c["name"] for c in _SELECTOR_CASES])
def test_selector_resolution_fixture(case: dict[str, Any]) -> None:
    elements = [_element(e) for e in case["elements"]]
    sel = cast(Selector, case["selector"])

    if not ("findAll" in case or "resolveUnique" in case):
        raise AssertionError(
            f"{case['name']}: neither findAll nor resolveUnique is asserted — a vacuous case"
        )

    if "findAll" in case:
        found = find_all(elements, sel)
        assert [e["identifier"] for e in found] == case["findAll"]["identifiers"]

    if "resolveUnique" in case:
        expect = case["resolveUnique"]
        if expect["outcome"] == "resolved":
            got = resolve_unique(elements, sel)
            assert got["identifier"] == expect["identifier"]
            if "frame" in expect:
                assert list(got["frame"]) == expect["frame"]
        elif expect["outcome"] == "notFound":
            with pytest.raises(ElementNotFound) as exc_info:
                resolve_unique(elements, sel)
            is_out_of_range = _OUT_OF_RANGE_MARKER in str(exc_info.value)
            assert is_out_of_range == (expect["reason"] == "outOfRange")
        elif expect["outcome"] == "ambiguous":
            with pytest.raises(AmbiguousSelector):
                resolve_unique(elements, sel)
        else:
            raise ValueError(f"unknown resolveUnique.outcome: {expect['outcome']!r}")


@pytest.mark.parametrize(
    "case", _DERIVED_LABEL_CASES, ids=[c["name"] for c in _DERIVED_LABEL_CASES]
)
def test_android_derived_label_fixture(case: dict[str, Any]) -> None:
    elements = parse_hierarchy(case["xml"])
    target_frame = tuple(case["targetFrame"])
    (element,) = (e for e in elements if e["frame"] == target_frame)
    assert element["label"] == case["expectedLabel"]

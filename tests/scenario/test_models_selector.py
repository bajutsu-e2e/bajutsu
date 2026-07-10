"""Tests for the scenario selector models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.drivers import base
from bajutsu.scenario import (
    Selector,
)


def test_selector_alias_and_as_selector() -> None:
    sel = Selector.model_validate({"idMatches": "result.row.*"})
    assert sel.id_matches == "result.row.*"
    assert sel.as_selector() == {"idMatches": "result.row.*"}


def test_selector_resolves_via_base() -> None:
    # Bridge a scenario Selector into base resolution (the determinism core).
    elements: list[base.Element] = [
        {
            "identifier": "settings.open",
            "label": "設定",
            "traits": ["button"],
            "value": None,
            "frame": (0.0, 0.0, 10.0, 10.0),
        },
    ]
    sel = Selector.model_validate({"id": "settings.open"})
    assert base.resolve_unique(elements, sel.as_selector())["label"] == "設定"


def test_empty_selector_rejected() -> None:
    with pytest.raises(ValidationError):
        Selector.model_validate({})


def test_id_candidate_list_round_trips() -> None:
    # A list of OR candidates (BE-0221) round-trips through the model into base resolution.
    sel = Selector.model_validate({"id": ["stable.refresh", "stable_refresh"]})
    assert sel.id == ["stable.refresh", "stable_refresh"]
    assert sel.as_selector() == {"id": ["stable.refresh", "stable_refresh"]}
    assert sel.first_id() == "stable.refresh"  # primary candidate for single-id consumers


def test_id_matches_candidate_list_round_trips() -> None:
    sel = Selector.model_validate({"idMatches": ["stable.row.*", "stable_row_*"]})
    assert sel.as_selector() == {"idMatches": ["stable.row.*", "stable_row_*"]}


@pytest.mark.parametrize("bad", [[], [""], ["ok", ""]])
def test_empty_or_blank_candidate_list_rejected(bad: list[str]) -> None:
    # An empty list or a blank entry reads as "a condition is set" yet matches nothing — reject it.
    with pytest.raises(ValidationError):
        Selector.model_validate({"id": bad})


def test_first_id_none_when_absent() -> None:
    assert Selector.model_validate({"label": "設定"}).first_id() is None


@pytest.mark.parametrize("field", ["id", "idMatches"])
def test_non_canonical_first_candidate_rejected(field: str) -> None:
    # The canonical (dotted SPEC) form must lead, since first_id / coverage / codegen take
    # candidate[0]; a dotted candidate after a non-dotted first one is the misordering (BE-0221).
    with pytest.raises(ValidationError, match="canonical"):
        Selector.model_validate({field: ["stable_refresh", "stable.refresh"]})


def test_dotted_first_and_all_underscore_lists_accepted() -> None:
    # Dotted-first is the norm; an all-underscore list (no dotted candidate to prioritize) is fine.
    assert Selector.model_validate({"id": ["stable.refresh", "stable_refresh"]}).first_id() == (
        "stable.refresh"
    )
    assert Selector.model_validate({"id": ["a_b", "c_d"]}).first_id() == "a_b"

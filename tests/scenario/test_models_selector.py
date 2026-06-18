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

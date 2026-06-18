"""Tests for the scenario action models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.scenario import (
    Swipe,
)


def test_swipe_forms() -> None:
    assert Swipe.model_validate({"on": {"id": "list"}, "direction": "up"}).direction == "up"
    assert Swipe.model_validate({"from": [1, 2], "to": [3, 4]}).to == (3, 4)
    with pytest.raises(ValidationError):  # mixing not allowed
        Swipe.model_validate({"on": {"id": "list"}, "from": [1, 2], "to": [3, 4]})

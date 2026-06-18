"""Tests for the scenario evidence-rule models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.scenario import (
    Scenario,
    Step,
)


def test_network_filter_domains() -> None:
    s = Scenario.model_validate(
        {
            "name": "n",
            "steps": [{"tap": {"id": "a"}}],
            "network": {"filter": {"domains": ["example.com", "api.example.com"]}},
        }
    )
    assert s.network is not None and s.network.filter is not None
    assert s.network.filter.domains == ["example.com", "api.example.com"]
    # Unset is allowed (shows every exchange in Steps).
    assert Scenario.model_validate({"name": "n", "steps": [{"tap": {"id": "a"}}]}).network is None


def test_capture_token_validation() -> None:
    Step.model_validate({"tap": {"id": "a"}, "capture": ["screenshot.after", "network"]})
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "a"}, "capture": ["bogus"]})
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "a"}, "capture": ["screenshot.whenever"]})


def test_capture_policy_and_redact() -> None:
    data = {
        "name": "rules",
        "steps": [{"tap": {"id": "a"}}],
        "capturePolicy": [
            {"on": {"action": "tap", "idMatches": "*.submit"}, "capture": ["network"]},
            {"on": {"event": "screenChanged"}, "capture": ["elements"]},
            {"on": {"result": "error"}, "capture": ["video"]},
        ],
        "redact": {"headers": ["Authorization"], "fields": ["token"]},
    }
    s = Scenario.model_validate(data)
    assert len(s.capture_policy) == 3
    assert s.redact is not None
    assert s.redact.headers == ["Authorization"]


def test_trigger_idmatches_requires_action() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [{"tap": {"id": "a"}}],
                "capturePolicy": [
                    {"on": {"event": "screenChanged", "idMatches": "*.x"}, "capture": ["elements"]}
                ],
            }
        )

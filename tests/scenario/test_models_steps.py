"""Tests for the scenario step (step / extract / if / forEach) models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.scenario import (
    Step,
)


def test_step_requires_exactly_one_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "a"}, "wait": {"for": {"id": "b"}, "timeout": 1}})
    with pytest.raises(ValidationError):
        Step.model_validate({"capture": ["screenshot"]})  # no action


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"tapp": {"id": "a"}})  # typo rejected by extra=forbid


def test_extract_on_step() -> None:
    step = Step.model_validate(
        {"tap": {"id": "counter.inc"}, "extract": {"count": {"sel": {"id": "counter.value"}}}}
    )
    assert step.extract is not None
    assert "count" in step.extract
    assert step.extract["count"].sel.id == "counter.value"
    assert step.extract["count"].prop == "value"


def test_extract_with_explicit_prop() -> None:
    step = Step.model_validate(
        {"tap": {"id": "ok"}, "extract": {"title": {"sel": {"id": "header"}, "prop": "label"}}}
    )
    assert step.extract is not None and step.extract["title"].prop == "label"


def test_extract_rejects_invalid_prop() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(
            {"tap": {"id": "ok"}, "extract": {"x": {"sel": {"id": "f"}, "prop": "color"}}}
        )


def test_extract_requires_sel() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "ok"}, "extract": {"x": {"prop": "value"}}})


def test_step_without_extract() -> None:
    step = Step.model_validate({"tap": {"id": "ok"}})
    assert step.extract is None


# --- if / forEach schema ---


def test_if_step_parses() -> None:
    step = Step.model_validate(
        {
            "if": {
                "condition": {"exists": {"id": "dialog"}},
                "then": [{"tap": {"id": "dialog.dismiss"}}],
                "else": [{"tap": {"id": "home.start"}}],
            },
        }
    )
    assert step.if_ is not None and len(step.if_.then) == 1 and step.if_.else_ is not None


def test_foreach_step_parses() -> None:
    step = Step.model_validate(
        {
            "forEach": {
                "sel": {"idMatches": "item.*"},
                "as": "current",
                "steps": [{"tap": {"id": "${vars.current}"}}],
            },
        }
    )
    assert step.for_each is not None and step.for_each.as_ == "current"


def test_if_rejects_capture_modifier() -> None:
    with pytest.raises(ValidationError, match="capture"):
        Step.model_validate(
            {
                "if": {"condition": {"exists": {"id": "x"}}, "then": []},
                "capture": ["screenshot.after"],
            }
        )


def test_foreach_rejects_extract_modifier() -> None:
    with pytest.raises(ValidationError, match="extract"):
        Step.model_validate(
            {
                "forEach": {"sel": {"id": "x"}, "as": "y", "steps": []},
                "extract": {"v": {"sel": {"id": "z"}}},
            }
        )


# --- web (WebView context) ---


def test_web_step_parses() -> None:
    step = Step.model_validate(
        {
            "web": {
                "within": {"id": "checkout.webview"},
                "steps": [
                    {"tap": {"id": "place-order"}},
                    {"assert": [{"exists": {"id": "order-confirmation"}}]},
                ],
            },
        }
    )
    assert step.web is not None
    assert step.web.within.id == "checkout.webview"
    assert len(step.web.steps) == 2


def test_web_step_requires_within() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(
            {
                "web": {
                    "steps": [{"tap": {"id": "ok"}}],
                },
            }
        )


def test_web_step_requires_steps() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(
            {
                "web": {
                    "within": {"id": "wv"},
                },
            }
        )


def test_web_step_in_step_actions() -> None:
    from bajutsu.scenario import STEP_ACTIONS

    assert "web" in STEP_ACTIONS


def test_web_rejects_capture_modifier() -> None:
    with pytest.raises(ValidationError, match="capture"):
        Step.model_validate(
            {
                "web": {
                    "within": {"id": "wv"},
                    "steps": [{"tap": {"id": "ok"}}],
                },
                "capture": ["screenshot.after"],
            }
        )


def test_web_rejects_extract_modifier() -> None:
    with pytest.raises(ValidationError, match="extract"):
        Step.model_validate(
            {
                "web": {
                    "within": {"id": "wv"},
                    "steps": [{"tap": {"id": "ok"}}],
                },
                "extract": {"v": {"sel": {"id": "z"}}},
            }
        )

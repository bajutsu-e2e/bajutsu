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


def test_back_step_parses() -> None:
    # A no-argument navigation action, expressed like the other no-arg steps (`back: {}`).
    step = Step.model_validate({"back": {}})
    assert step.back is not None


def test_back_step_is_one_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"back": {}, "tap": {"id": "a"}})


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


def test_manual_step_parses_unreproducible() -> None:
    # A human-takeover marker (BE-0185) with no deterministic bypass: `bypass` defaults to None.
    step = Step.model_validate({"manual": {"label": "solve the CAPTCHA"}})
    assert step.manual is not None
    assert step.manual.label == "solve the CAPTCHA"
    assert step.manual.bypass is None


def test_manual_step_parses_bypassable() -> None:
    step = Step.model_validate(
        {"manual": {"label": "approve Face ID", "bypass": "disable biometrics behind a test flag"}}
    )
    assert step.manual is not None
    assert step.manual.bypass == "disable biometrics behind a test flag"


def test_manual_step_requires_label() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"manual": {}})


def test_manual_step_is_one_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"manual": {"label": "x"}, "tap": {"id": "a"}})


def test_clear_step_parses() -> None:
    step = Step.model_validate({"clear": {"into": {"id": "form.note"}}})
    assert step.clear is not None
    assert step.clear.into is not None and step.clear.into.id == "form.note"


def test_delete_step_parses() -> None:
    step = Step.model_validate({"delete": {"into": {"id": "form.note"}, "count": 3}})
    assert step.delete is not None
    assert step.delete.count == 3
    assert step.delete.into.id == "form.note"


def test_delete_rejects_non_positive_count() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"delete": {"into": {"id": "f"}, "count": 0}})


def test_select_step_parses_mode_all_default() -> None:
    step = Step.model_validate({"select": {"into": {"id": "form.note"}}})
    assert step.select is not None
    assert step.select.mode == "all"
    assert step.select.into.id == "form.note"


def test_select_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"select": {"into": {"id": "f"}, "mode": "word"}})


def test_copy_step_parses() -> None:
    # A no-argument action expressed like the other no-arg steps (`copy: {}`).
    step = Step.model_validate({"copy": {}})
    assert step.copy_ is not None


def test_text_editing_steps_are_one_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"copy": {}, "clear": {"into": {"id": "a"}}})


# --- handleSystemAlert (BE-0316) -----------------------------------------------------------------


def test_handle_system_alert_parses_with_label_selector() -> None:
    step = Step.model_validate({"handleSystemAlert": {"sel": {"label": "Allow"}, "timeout": 5}})
    assert step.handle_system_alert is not None
    assert step.handle_system_alert.sel.label == "Allow"
    assert step.handle_system_alert.timeout == 5.0


def test_handle_system_alert_accepts_labelmatches_and_index() -> None:
    for sel in ({"labelMatches": "Allo.*"}, {"label": "OK", "index": 1}):
        step = Step.model_validate({"handleSystemAlert": {"sel": sel, "timeout": 5}})
        assert step.handle_system_alert is not None


@pytest.mark.parametrize(
    "field",
    [
        {"id": "perm.allow"},
        {"idMatches": "perm.*"},
        {"traits": ["button"]},
        {"value": "Allow"},
        {"within": {"label": "Alert"}},
    ],
)
def test_handle_system_alert_rejects_non_label_selector_fields(field: dict[str, object]) -> None:
    # A SpringBoard alert button carries only its visible text, so any id/trait/value/within field is
    # a scenario error caught at parse time (§6.2), not a match that can never succeed at run time.
    with pytest.raises(ValidationError, match="handleSystemAlert sel accepts only"):
        Step.model_validate({"handleSystemAlert": {"sel": field, "timeout": 5}})


def test_handle_system_alert_requires_timeout() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"handleSystemAlert": {"sel": {"label": "Allow"}}})


def test_handle_system_alert_is_one_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(
            {"handleSystemAlert": {"sel": {"label": "Allow"}, "timeout": 5}, "tap": {"id": "a"}}
        )

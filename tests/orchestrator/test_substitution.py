"""Tests for runtime token substitution into steps and assertions (_interp_step/_interp_asserts)."""

from __future__ import annotations


def test_interp_step_skips_model_dump_when_no_tokens() -> None:
    """When a step contains no ${...} tokens, _interp_step should avoid the
    expensive model_dump() call by using a cheaper pre-check."""
    from unittest.mock import patch

    from bajutsu.orchestrator import _interp_step
    from bajutsu.scenario import Step

    step = Step.model_validate({"tap": {"id": "home.title"}})
    bindings = {"secrets.token": "SECRET"}

    with patch.object(Step, "model_dump", wraps=step.model_dump) as mock_dump:
        result = _interp_step(step, bindings)
        # The step has no tokens, so model_dump should not be called.
        mock_dump.assert_not_called()
    # The original step is returned unchanged.
    assert result is step


def test_interp_step_still_substitutes_when_tokens_present() -> None:
    """When a step does contain a matching token, interpolation still works."""
    from bajutsu.orchestrator import _interp_step
    from bajutsu.scenario import Step

    step = Step.model_validate({"tap": {"id": "${secrets.target}"}})
    bindings = {"secrets.target": "home.title"}

    result = _interp_step(step, bindings)
    assert result is not step
    assert result.tap is not None and result.tap.id == "home.title"


def test_interp_step_returns_early_with_empty_bindings() -> None:
    """With empty bindings, _interp_step returns the original step immediately."""
    from bajutsu.orchestrator import _interp_step
    from bajutsu.scenario import Step

    step = Step.model_validate({"tap": {"id": "ok"}})
    result = _interp_step(step, {})
    assert result is step


def test_interp_asserts_skips_model_dump_when_no_tokens() -> None:
    """When assertions contain no ${...} tokens, _interp_asserts should avoid
    the expensive model_dump() call by using a cheaper pre-check."""
    from unittest.mock import patch

    from bajutsu.orchestrator import _interp_asserts
    from bajutsu.scenario import Assertion

    asserts = [Assertion.model_validate({"exists": {"id": "home.title"}})]
    bindings = {"secrets.token": "SECRET"}

    with patch.object(Assertion, "model_dump", wraps=asserts[0].model_dump) as mock_dump:
        result = _interp_asserts(asserts, bindings)
        mock_dump.assert_not_called()
    assert result is asserts


def test_interp_asserts_substitutes_when_tokens_present() -> None:
    """When an assertion contains a matching token, interpolation works."""
    from bajutsu.orchestrator import _interp_asserts
    from bajutsu.scenario import Assertion

    asserts = [Assertion.model_validate({"value": {"sel": {"id": "f"}, "equals": "${secrets.v}"}})]
    bindings = {"secrets.v": "hello"}

    result = _interp_asserts(asserts, bindings)
    assert result is not asserts
    assert result[0].value is not None and result[0].value.equals == "hello"

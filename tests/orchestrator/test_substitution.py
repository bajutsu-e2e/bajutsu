"""Tests for runtime token substitution into steps and assertions (_interp_step/_interp_asserts)."""

from __future__ import annotations


def test_interp_step_returns_step_unchanged_when_it_has_no_tokens() -> None:
    """A token-free step is returned unchanged even when bindings are present — returning the same
    object (not a copy) is the observable contract of the no-substitution fast path."""
    from bajutsu.orchestrator import _interp_step
    from bajutsu.scenario import Step

    step = Step.model_validate({"tap": {"id": "home.title"}})
    result = _interp_step(step, {"secrets.token": "SECRET"})
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


def test_interp_asserts_returns_list_unchanged_when_no_tokens() -> None:
    """A token-free assertion list is returned unchanged even when bindings are present — the same
    list object comes back from the no-substitution fast path."""
    from bajutsu.orchestrator import _interp_asserts
    from bajutsu.scenario import Assertion

    asserts = [Assertion.model_validate({"exists": {"id": "home.title"}})]
    result = _interp_asserts(asserts, {"secrets.token": "SECRET"})
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

"""Tests for the `totp` step: model parsing + producing a code into vars.* (BE-0046)."""

from __future__ import annotations

from bajutsu.orchestrator.actions.handlers.totp import _do_totp
from bajutsu.orchestrator.substitution import _interp_step
from bajutsu.scenario import Step
from bajutsu.totp import totp

_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_totp_step_parses() -> None:
    step = Step.model_validate({"totp": {"secret": _SECRET, "into": {"var": "code"}}})
    assert step.totp is not None
    assert step.totp.secret == _SECRET and step.totp.into.var == "code"


def test_totp_step_is_an_exclusive_action() -> None:
    # totp is an action, so it can't share a step with another action.
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Step.model_validate({"totp": {"secret": _SECRET, "into": {"var": "c"}}, "tap": {"id": "a"}})


def test_totp_step_writes_a_six_digit_code_into_vars() -> None:
    step = Step.model_validate({"totp": {"secret": _SECRET, "into": {"var": "code"}}})
    bindings: dict[str, str] = {}
    _do_totp(None, step, None, None, bindings)
    assert bindings["vars.code"].isdigit() and len(bindings["vars.code"]) == 6


def test_totp_code_matches_the_pure_function(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Pin the clock so the step's code equals the gate-tested pure function at that instant.
    monkeypatch.setattr("bajutsu.orchestrator.actions.handlers.totp.time.time", lambda: 1111111109)
    step = Step.model_validate({"totp": {"secret": _SECRET, "into": {"var": "code"}}})
    bindings: dict[str, str] = {}
    _do_totp(None, step, None, None, bindings)
    assert bindings["vars.code"] == totp(_SECRET, now=1111111109)


def test_totp_secret_is_interpolated_before_the_handler() -> None:
    # `${secrets.*}` is substituted (generically, by _interp_step) before the handler runs, so the
    # raw seed never sits in the executed step's recorded form — only the resolved copy runs.
    step = Step.model_validate({"totp": {"secret": "${secrets.SEED}", "into": {"var": "code"}}})
    resolved = _interp_step(step, {"secrets.SEED": _SECRET})
    assert resolved.totp is not None and resolved.totp.secret == _SECRET


def test_totp_without_bindings_is_a_noop() -> None:
    # No var scope (e.g. a bare condition eval): nothing to write, and it must not crash.
    step = Step.model_validate({"totp": {"secret": _SECRET, "into": {"var": "code"}}})
    _do_totp(None, step, None, None, None)

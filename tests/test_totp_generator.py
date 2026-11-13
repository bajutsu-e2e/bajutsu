"""Tests for named TOTP generators: `totp` with `name`, and the `otp` step that reads one back."""

from __future__ import annotations

import base64
from functools import partial

import pytest
from conftest import el
from pydantic import ValidationError

from bajutsu.codegen.common import totp_todo
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.actions._registry import _step_label
from bajutsu.common.orchestrator.actions.handlers.totp import _do_otp, _do_totp, generator_key
from bajutsu.common.orchestrator.loop import _functions
from bajutsu.common.orchestrator.loop._functions import _wait_for_totp_window
from bajutsu.common.orchestrator.substitution import _interp_step
from bajutsu.common.scenario import (
    Component,
    Scenario,
    Step,
    expand_components,
    redact_totp_secrets,
)
from bajutsu.common.totp import totp

# The RFC 6238 test seed, built rather than written so no key-shaped literal sits in the file.
_SEED = base64.b32encode(b"1234567890" * 2).decode()
_BOUNDARY = 1_800_000_000.0
# The clock the handlers read, patched by dotted path so a test pins the generated code.
_HANDLER_TIME = "bajutsu.common.orchestrator.actions.handlers.totp.time.time"


def _typed(driver: FakeDriver) -> list[object]:
    return [arg for kind, arg in driver.actions if kind == "type"]


# --- the models ---


def test_totp_name_registers_without_into() -> None:
    step = Step.model_validate({"totp": {"secret": _SEED, "name": "main"}})
    assert step.totp is not None
    assert step.totp.name == "main" and step.totp.into is None


def test_totp_needs_into_or_name() -> None:
    with pytest.raises(ValidationError, match="`into`"):
        Step.model_validate({"totp": {"secret": _SEED}})


def test_min_remaining_needs_into() -> None:
    # A register-only step produces no code, so a window wait would keep nothing fresh.
    with pytest.raises(ValidationError, match="minRemaining needs `into`"):
        Step.model_validate({"totp": {"secret": _SEED, "name": "main", "minRemaining": 10}})


def test_otp_parses() -> None:
    step = Step.model_validate(
        {"otp": {"from": "main", "into": {"var": "code"}, "minRemaining": 10}}
    )
    assert step.otp is not None
    assert (step.otp.from_, step.otp.into.var, step.otp.min_remaining) == ("main", "code", 10)


@pytest.mark.parametrize("value", [0, 31, True, "10"])
def test_otp_min_remaining_is_bounded_and_strict(value: object) -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"otp": {"from": "main", "into": {"var": "c"}, "minRemaining": value}})


def test_otp_out_of_window_names_the_step_kind() -> None:
    with pytest.raises(ValidationError, match="otp: minRemaining must be between 1 and 30"):
        Step.model_validate({"otp": {"from": "main", "into": {"var": "c"}, "minRemaining": 31}})


def test_otp_is_an_exclusive_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"otp": {"from": "main", "into": {"var": "c"}}, "tap": {"id": "a"}})


@pytest.mark.parametrize("otp", [{"into": {"var": "c"}}, {"from": "", "into": {"var": "c"}}])
def test_otp_requires_a_generator_name(otp: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"otp": otp})


# --- the handlers ---


def test_totp_name_registers_the_secret_and_still_writes_into() -> None:
    step = Step.model_validate({"totp": {"secret": _SEED, "name": "main", "into": {"var": "c"}}})
    bindings: dict[str, str] = {}
    _do_totp(FakeDriver([]), step, None, None, bindings)
    assert bindings[generator_key("main")] == _SEED
    assert bindings["vars.c"].isdigit()


def test_otp_writes_the_current_code_of_the_named_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1111111109.0]
    monkeypatch.setattr(_HANDLER_TIME, lambda: clock[0])
    bindings: dict[str, str] = {}
    _do_totp(
        FakeDriver([]),
        Step.model_validate({"totp": {"secret": _SEED, "name": "main"}}),
        None,
        None,
        bindings,
    )
    # Later in the run, in a different window: the code follows the clock at the `otp` step.
    clock[0] += 120
    otp = Step.model_validate({"otp": {"from": "main", "into": {"var": "code"}}})
    _do_otp(FakeDriver([]), otp, None, None, bindings)
    assert bindings["vars.code"] == totp(_SEED, now=clock[0])
    assert bindings["vars.code"] != totp(_SEED, now=clock[0] - 120)


def test_otp_without_a_registered_generator_fails_the_step() -> None:
    otp = Step.model_validate({"otp": {"from": "main", "into": {"var": "code"}}})
    with pytest.raises(base.SelectorError, match="no totp generator named 'main'"):
        _do_otp(FakeDriver([]), otp, None, None, {})


def test_otp_without_bindings_is_a_noop() -> None:
    otp = Step.model_validate({"otp": {"from": "main", "into": {"var": "code"}}})
    _do_otp(FakeDriver([]), otp, None, None, None)


def test_a_register_only_step_still_rejects_a_bad_secret() -> None:
    # The secret is checked when it is registered, so the `totp` step fails, not a later `otp`.
    secret = "not!valid!base32"
    step = Step.model_validate({"totp": {"secret": secret, "name": "main"}})
    bindings: dict[str, str] = {}
    with pytest.raises(base.SelectorError) as exc:
        _do_totp(FakeDriver([]), step, None, None, bindings)
    assert secret not in str(exc.value)
    assert generator_key("main") not in bindings


def test_no_token_can_read_a_registered_secret() -> None:
    # The key holds a `}`, which the `${...}` grammar cannot contain, so no spelling reaches it.
    bindings = {generator_key("main"): _SEED}
    for text in ("${totp}main}", "${totp.main}", "${totp}", "${main}"):
        step = Step.model_validate({"type": {"text": text, "into": {"id": "f"}}})
        resolved = _interp_step(step, bindings)
        assert resolved.type is not None and _SEED not in resolved.type.text


def test_the_otp_snapshot_carries_no_secret() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "otp",
            "steps": [
                {"totp": {"secret": _SEED, "name": "main"}},
                {"otp": {"from": "main", "into": {"var": "code"}}},
            ],
        }
    )
    redacted = redact_totp_secrets(scenario)
    assert _SEED not in redacted.model_dump_json(by_alias=True)
    assert redacted.model_dump(by_alias=True)["steps"][1]["otp"]["from"] == "main"


def test_a_name_reused_for_another_secret_fails_the_step() -> None:
    other = base64.b32encode(b"abcdefghij" * 2).decode()
    bindings = {generator_key("main"): _SEED}
    step = Step.model_validate({"totp": {"secret": other, "name": "main"}})
    with pytest.raises(base.SelectorError, match="already registered") as exc:
        _do_totp(FakeDriver([]), step, None, None, bindings)
    assert other not in str(exc.value) and _SEED not in str(exc.value)
    assert bindings[generator_key("main")] == _SEED


def test_the_same_secret_registers_again() -> None:
    bindings = {generator_key("main"): _SEED}
    _do_totp(
        FakeDriver([]),
        Step.model_validate({"totp": {"secret": _SEED, "name": "main"}}),
        None,
        None,
        bindings,
    )
    assert bindings[generator_key("main")] == _SEED


# --- the run loop ---


def _no_components(ref: str) -> Component:
    raise AssertionError(f"no component {ref!r} expected")


def _scenario(*steps: dict[str, object]) -> Scenario:
    return Scenario.model_validate({"name": "otp", "steps": list(steps)})


def test_a_scenario_fetches_codes_later_from_one_registration() -> None:
    scenario = _scenario(
        {"totp": {"secret": "${secrets.SEED}", "name": "main"}},
        {"tap": {"id": "x"}},
        {"otp": {"from": "main", "into": {"var": "first"}}},
        {"type": {"text": "${vars.first}", "into": {"id": "field"}}},
        {"otp": {"from": "main", "into": {"var": "second"}}},
        {"type": {"text": "${vars.second}", "into": {"id": "field"}}},
    )
    driver = FakeDriver([el("x", "X"), el("field", "Field")])
    result = run_scenario(driver, scenario, bindings={"secrets.SEED": _SEED})
    assert result.ok, result.failure
    typed = _typed(driver)
    assert len(typed) == 2 and all(isinstance(t, str) and t.isdigit() for t in typed)


def test_otp_before_its_totp_fails_the_run() -> None:
    scenario = _scenario(
        {"otp": {"from": "main", "into": {"var": "code"}}},
        {"totp": {"secret": _SEED, "name": "main"}},
    )
    result = run_scenario(FakeDriver([el("x", "X")]), scenario)
    assert not result.ok
    assert "no totp generator named 'main'" in result.steps[0].reason


def test_a_skipped_branch_registers_nothing() -> None:
    scenario = _scenario(
        {
            "if": {
                "condition": {"exists": {"id": "absent"}},
                "then": [{"totp": {"secret": _SEED, "name": "main"}}],
            }
        },
        {"otp": {"from": "main", "into": {"var": "code"}}},
    )
    result = run_scenario(FakeDriver([el("x", "X")]), scenario)
    assert not result.ok
    assert "no totp generator named 'main'" in (result.failure or "")


@pytest.mark.parametrize(
    "register",
    [
        {"group": {"name": "sign in", "steps": [{"totp": {"secret": _SEED, "name": "main"}}]}},
        {
            "if": {
                "condition": {"exists": {"id": "x"}},
                "then": [{"totp": {"secret": _SEED, "name": "main"}}],
            }
        },
    ],
)
def test_a_generator_registered_in_a_body_outlives_it(register: dict[str, object]) -> None:
    scenario = _scenario(
        register,
        {"otp": {"from": "main", "into": {"var": "code"}}},
        {"type": {"text": "${vars.code}", "into": {"id": "field"}}},
    )
    # A `group` is expanded at load time, as `bajutsu run` does before the loop sees it.
    expand_components([scenario], _no_components)
    driver = FakeDriver([el("x", "X"), el("field", "Field")])
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert len(_typed(driver)) == 1


def test_a_generator_registered_in_setup_serves_the_steps() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "otp",
            "setup": [{"totp": {"secret": _SEED, "name": "main"}}],
            "steps": [{"otp": {"from": "main", "into": {"var": "code"}}}],
        }
    )
    result = run_scenario(FakeDriver([el("x", "X")]), scenario)
    assert result.ok, result.failure


class _Clocks:
    """A monotonic fake clock whose wall clock moves with it, so a window wait ends at once."""

    def __init__(self, phase: float) -> None:
        self.mono = 0.0
        self.wall_t = _BOUNDARY + phase

    def now(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.mono += seconds
        self.wall_t += seconds

    def wall(self) -> float:
        return self.wall_t


def _run_on(c: _Clocks, monkeypatch: pytest.MonkeyPatch, *steps: dict[str, object]) -> FakeDriver:
    monkeypatch.setattr(
        _functions, "_wait_for_totp_window", partial(_wait_for_totp_window, wall=c.wall)
    )
    monkeypatch.setattr(_HANDLER_TIME, c.wall)
    driver = FakeDriver([el("x", "X"), el("field", "Field")])
    result = run_scenario(driver, _scenario(*steps), clock=c)
    assert result.ok, result.failure
    return driver


def test_otp_min_remaining_waits_for_the_next_window(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Clocks(phase=25)
    driver = _run_on(
        c,
        monkeypatch,
        {"totp": {"secret": _SEED, "name": "main"}},
        {"otp": {"from": "main", "into": {"var": "code"}, "minRemaining": 10}},
        {"type": {"text": "${vars.code}", "into": {"id": "field"}}},
    )
    assert c.wall_t == pytest.approx(_BOUNDARY + 30)
    assert _typed(driver) == [totp(_SEED, now=_BOUNDARY + 30)]


def test_otp_without_min_remaining_never_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Clocks(phase=29)
    _run_on(
        c,
        monkeypatch,
        {"totp": {"secret": _SEED, "name": "main"}},
        {"otp": {"from": "main", "into": {"var": "code"}}},
    )
    assert c.wall_t == pytest.approx(_BOUNDARY + 29)


def test_an_unregistered_otp_fails_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Clocks(phase=25)
    monkeypatch.setattr(
        _functions, "_wait_for_totp_window", partial(_wait_for_totp_window, wall=c.wall)
    )
    scenario = _scenario({"otp": {"from": "main", "into": {"var": "code"}, "minRemaining": 10}})
    result = run_scenario(FakeDriver([el("x", "X")]), scenario, clock=c)
    assert not result.ok
    assert c.wall_t == pytest.approx(_BOUNDARY + 25)


# --- the live label and codegen ---


def test_the_otp_label_names_the_requirement() -> None:
    step = Step.model_validate(
        {"otp": {"from": "main", "into": {"var": "code"}, "minRemaining": 10}}
    )
    assert _step_label(step, "otp") == "otp (≥10s left)"


@pytest.mark.parametrize(
    ("step", "todo"),
    [
        (
            {"otp": {"from": "main", "into": {"var": "code"}}},
            "// TODO: otp(from: main, into: code) — RFC 6238 OTP; not generated",
        ),
        (
            {"totp": {"secret": _SEED, "name": "main"}},
            "// TODO: totp(name: main) — RFC 6238 OTP; not generated",
        ),
        (
            {"totp": {"secret": _SEED, "into": {"var": "code"}}},
            "// TODO: totp(into: code) — RFC 6238 OTP; not generated",
        ),
        ({"tap": {"id": "a"}}, None),
    ],
)
def test_codegen_names_the_generator_never_the_secret(
    step: dict[str, object], todo: str | None
) -> None:
    assert totp_todo(Step.model_validate(step)) == todo

"""Tests for the `generate` step: schema validation + producing a value into vars.* (BE-0377)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.scenario import Step
from bajutsu.orchestrator.actions.handlers.generate import (
    _datetime_value,
    _do_generate,
    generated_value,
)

# A fixed instant every datetime expectation below is derived from, so the assertions state the
# arithmetic rather than re-reading the clock.
_PINNED = datetime(2026, 8, 18, 23, 30, 0, tzinfo=UTC)


def _step(payload: dict[str, object], var: str = "v") -> Step:
    return Step.model_validate({"generate": {**payload, "into": {"var": var}}})


def _produce(payload: dict[str, object]) -> str:
    bindings: dict[str, str] = {}
    _do_generate(FakeDriver([]), _step(payload), None, None, bindings)
    return bindings["vars.v"]


# --- schema ---


def test_generate_is_an_exclusive_action() -> None:
    # generate is an action, so it can't share a step with another action.
    with pytest.raises(ValidationError):
        Step.model_validate(
            {"generate": {"random": {"uuid": {}}, "into": {"var": "v"}}, "tap": {"id": "a"}}
        )


def test_exactly_one_generator_kind_is_required() -> None:
    payloads: list[dict[str, object]] = [{}, {"random": {"uuid": {}}, "datetime": {}}]
    for payload in payloads:
        with pytest.raises(ValidationError):
            _step(payload)


def test_exactly_one_random_kind_is_required() -> None:
    for random in ({}, {"uuid": {}, "int": {"min": 1, "max": 2}}):
        with pytest.raises(ValidationError):
            _step({"random": random})


def test_random_ranges_and_length_are_validated_at_load_time() -> None:
    for random in (
        {"string": {"length": 0}},
        {"string": {"length": 4, "charset": "base64"}},
        {"int": {"min": 5, "max": 1}},
        {"float": {"min": 2.0, "max": 1.0}},
        {"float": {"min": 0.0, "max": 1.0, "precision": -1}},
    ):
        with pytest.raises(ValidationError):
            _step({"random": random})


def test_an_unknown_timezone_is_rejected_when_the_scenario_loads() -> None:
    # Directive 2: an unresolvable zone fails the load, never silently falls back to UTC mid-run.
    with pytest.raises(ValidationError, match="not a known IANA zone"):
        _step({"datetime": {"timezone": "Nowhere/Nope"}})


def test_an_empty_format_is_rejected_when_the_scenario_loads() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        _step({"datetime": {"format": "  "}})


def test_int_and_float_keep_their_yaml_keys() -> None:
    # The fields are suffixed in Python (they shadow builtins); the scenario still says `int`/`float`.
    step = _step({"random": {"int": {"min": 1, "max": 1}}})
    assert step.generate is not None and step.generate.random is not None
    assert step.generate.random.int_ is not None
    dumped = step.model_dump(by_alias=True, exclude_none=True)
    assert dumped["generate"]["random"]["int"] == {"min": 1, "max": 1}


# --- produced values ---


def test_random_string_honors_length_and_charset() -> None:
    assert re.fullmatch(r"[A-Za-z0-9]{8}", _produce({"random": {"string": {"length": 8}}}))
    assert re.fullmatch(
        r"[0-9a-f]{12}", _produce({"random": {"string": {"length": 12, "charset": "hex"}}})
    )
    assert re.fullmatch(
        r"[A-Za-z]{5}", _produce({"random": {"string": {"length": 5, "charset": "alpha"}}})
    )
    assert re.fullmatch(
        r"[0-9]{6}", _produce({"random": {"string": {"length": 6, "charset": "numeric"}}})
    )


def test_random_int_stays_inside_the_inclusive_range() -> None:
    values = {int(_produce({"random": {"int": {"min": 3, "max": 5}}})) for _ in range(50)}
    assert values <= {3, 4, 5}
    assert _produce({"random": {"int": {"min": 7, "max": 7}}}) == "7"


def test_random_float_honors_range_and_precision() -> None:
    text = _produce({"random": {"float": {"min": 1.0, "max": 2.0, "precision": 2}}})
    # `precision` fixes the decimal places, trailing zeros included — 1.5 renders as "1.50".
    assert re.fullmatch(r"[12]\.[0-9]{2}", text)
    assert 1.0 <= float(text) <= 2.0
    assert 0.0 <= float(_produce({"random": {"float": {"min": 0.0, "max": 1.0}}})) <= 1.0


def test_random_uuid_is_version_4() -> None:
    from uuid import UUID

    assert UUID(_produce({"random": {"uuid": {}}})).version == 4


def test_two_draws_of_the_same_step_differ() -> None:
    # The motivation for the step: a value no earlier run already took.
    payload: dict[str, object] = {"random": {"string": {"length": 16}}}
    assert len({_produce(payload) for _ in range(20)}) > 1


def test_datetime_defaults_to_an_iso_8601_utc_stamp() -> None:
    step = _step({"datetime": {}})
    assert step.generate is not None and step.generate.datetime is not None
    assert _datetime_value(step.generate.datetime, now=_PINNED) == "2026-08-18T23:30:00+00:00"


def test_datetime_applies_format_and_additive_offsets() -> None:
    step = _step({"datetime": {"format": "%Y-%m-%d %H:%M", "offsetDays": 1, "offsetHours": -1}})
    assert step.generate is not None and step.generate.datetime is not None
    assert _datetime_value(step.generate.datetime, now=_PINNED) == "2026-08-19 22:30"


def test_datetime_computes_in_the_named_zone() -> None:
    # 23:30 UTC is still the previous day in Los Angeles — the reason a scenario names its zone.
    step = _step({"datetime": {"format": "%Y-%m-%d", "timezone": "America/Los_Angeles"}})
    assert step.generate is not None and step.generate.datetime is not None
    assert _datetime_value(step.generate.datetime, now=_PINNED) == "2026-08-18"


def test_datetime_offset_is_applied_after_the_zone_conversion() -> None:
    step = _step(
        {"datetime": {"format": "%Y-%m-%d", "offsetDays": 1, "timezone": "America/Los_Angeles"}}
    )
    assert step.generate is not None and step.generate.datetime is not None
    assert _datetime_value(step.generate.datetime, now=_PINNED) == "2026-08-19"


# --- the handler's contract ---


def test_the_value_lands_in_the_named_var() -> None:
    bindings: dict[str, str] = {}
    _do_generate(
        FakeDriver([]), _step({"random": {"uuid": {}}}, var="orderRef"), None, None, bindings
    )
    assert list(bindings) == ["vars.orderRef"]


def test_generate_without_bindings_is_a_noop() -> None:
    # No var scope (e.g. a bare condition eval): nothing to write, and it must not crash.
    _do_generate(FakeDriver([]), _step({"random": {"uuid": {}}}), None, None, None)


def test_every_accepted_step_produces_a_value() -> None:
    # Directive 2, flow determinism: a step the validator accepted always executes and succeeds.
    payloads: tuple[dict[str, object], ...] = (
        {"random": {"string": {"length": 1}}},
        {"random": {"int": {"min": -5, "max": -1}}},
        {"random": {"float": {"min": -1.5, "max": 1.5, "precision": 0}}},
        {"random": {"uuid": {}}},
        {"datetime": {}},
        {"datetime": {"format": "%H:%M:%S", "offsetSeconds": 90, "offsetMinutes": -1}},
    )
    for payload in payloads:
        step = _step(payload)
        assert step.generate is not None
        assert generated_value(step.generate)

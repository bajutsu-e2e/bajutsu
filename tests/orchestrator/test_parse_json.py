"""Tests for the `parseJson` step: the model, path resolution, and derived secrets (BE-0516)."""

from __future__ import annotations

import json

import pytest
from _orch import FakeClock, _scenario
from conftest import el
from pydantic import ValidationError

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.actions import _do_action
from bajutsu.common.orchestrator.actions.handlers import parse_json
from bajutsu.common.orchestrator.actions.handlers.parse_json import (
    ParseJsonError,
    evaluate,
    record_parse_json,
    target_names,
)
from bajutsu.common.orchestrator.substitution import _interp_step
from bajutsu.common.orchestrator.types import DerivedSecrets
from bajutsu.common.scenario import ParseJson, Step


def _spec(*entries: tuple[str, str], source: str = "${vars.body}") -> ParseJson:
    return ParseJson.model_validate(
        {"from": source, "extract": [{"var": v, "path": p} for v, p in entries]}
    )


def _step(var: str, path: str, source: str) -> dict[str, object]:
    return {"parseJson": {"from": source, "extract": [{"var": var, "path": path}]}}


def _eval_over(spec: ParseJson, body: object, **extra: str) -> dict[str, str]:
    text = body if isinstance(body, str) else json.dumps(body)
    return evaluate(spec, {"vars.body": text, **{f"vars.{k}": v for k, v in extra.items()}})


def test_nested_values_resolve_and_non_strings_become_compact_json() -> None:
    spec = _spec(
        ("code", "data.items[0].code"),
        ("userId", "data.user.id"),
        ("user", "data.user"),
        ("flag", "data.flag"),
    )
    body = {"data": {"items": [{"code": "A1"}], "user": {"id": 42}, "flag": None}}
    assert _eval_over(spec, body) == {
        "vars.code": "A1",
        "vars.userId": "42",
        "vars.user": '{"id":42}',
        "vars.flag": "null",
    }


def test_a_leading_and_chained_index_resolve() -> None:
    assert _eval_over(_spec(("v", "[0][1]")), [[10, 20]]) == {"vars.v": "20"}


def test_a_token_supplies_one_path_segment_and_a_var_name() -> None:
    spec = _spec(("${vars.name}", "items[${vars.i}].id"))
    assert _eval_over(spec, {"items": [{"id": "x"}, {"id": "y"}]}, i="1", name="got") == {
        "vars.got": "y"
    }
    assert target_names(spec, {"vars.name": "got"}) == ["got"]


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("", "empty path"),
        (".a", "empty key"),
        ("a.", "empty key"),
        ("a[0", "unterminated"),
        ("a[-1]", "not a non-negative integer"),
        ("a[99999999999]", "out of range"),
        ("a]", "unexpected"),
        ("missing", "the key is missing"),
        ("a.b", "key is applied to a non-object"),
        ("o[0]", "index is applied to a non-array"),
        ("a[5]", "the index is out of range"),
    ],
)
def test_a_bad_path_fails_without_quoting_the_text(path: str, reason: str) -> None:
    with pytest.raises(ParseJsonError, match=reason) as caught:
        _eval_over(_spec(("v", path)), {"a": ["s3cret-value"], "o": {}})
    assert "s3cret-value" not in str(caught.value)


@pytest.mark.parametrize("value", ["a.b", "a[0]", "a]", ""])
def test_a_substituted_segment_may_not_carry_path_structure_and_is_not_quoted(value: str) -> None:
    with pytest.raises(ParseJsonError, match="exactly one non-empty path segment") as caught:
        _eval_over(_spec(("v", "o.${vars.k}")), {"o": {}}, k=value)
    assert not value or value not in str(caught.value).replace("'.', '[', or ']'", "")


@pytest.mark.parametrize(
    "text", ["not json s3cret", "[" * 100_000, '{"a": NaN}', '{"a": -Infinity}']
)
def test_text_that_is_not_json_fails_without_quoting_it(text: str) -> None:
    with pytest.raises(ParseJsonError, match="not valid JSON") as caught:
        _eval_over(_spec(("v", "a")), text)
    assert "s3cret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_text_over_the_input_cap_fails() -> None:
    with pytest.raises(ParseJsonError, match="exceeds 1048576 characters"):
        _eval_over(_spec(("v", "a")), '{"a":"' + "x" * 1_048_576 + '"}')


def test_an_unbound_token_fails_naming_the_token() -> None:
    with pytest.raises(ParseJsonError, match=r"\$\{vars\.nope\}"):
        _eval_over(_spec(("v", "${vars.nope}")), {})


@pytest.mark.parametrize(
    ("first", "reason"),
    [("", "empty name"), ("same", "substitute to the same var")],
)
def test_a_substituted_var_name_is_re_checked(first: str, reason: str) -> None:
    spec = _spec(("${vars.name}", "a"), ("same", "a"))
    with pytest.raises(ParseJsonError, match=reason):
        _eval_over(spec, {"a": 1}, name=first)


def test_a_literal_duplicate_var_is_a_load_error() -> None:
    with pytest.raises(ValidationError, match="used by more than one extract entry"):
        _spec(("v", "a"), ("v", "b"))


def test_an_empty_extract_list_is_a_load_error() -> None:
    with pytest.raises(ValidationError):
        _spec()


def test_the_runner_leaves_parse_json_authored_at_any_depth() -> None:
    # A substituted value must not reach the model's validator, which would quote it.
    authored = _step("${vars.name}", "a", "${vars.body}")
    nested = Step.model_validate({"forEach": {"sel": {"id": "x"}, "as": "e", "steps": [authored]}})
    bindings = {"vars.name": "n", "vars.body": "{}"}
    done = _interp_step(nested, bindings)
    assert done.for_each is not None
    assert done.for_each.steps[0] == Step.model_validate(authored)


def test_a_run_writes_the_values_for_later_steps_and_records_none_of_them() -> None:
    driver = FakeDriver([el("x", "X", value="tok")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "parse",
                "steps": [
                    _step("token", "data.token", '{"data":{"token":"tok"}}'),
                    {"assert": [{"value": {"sel": {"id": "x"}, "equals": "${vars.token}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert result.steps[0].generated is None
    assert result.derived_secrets == []


def test_a_failing_entry_leaves_every_variable_unchanged() -> None:
    step = Step.model_validate(
        {
            "parseJson": {
                "from": '{"a":"ok"}',
                "extract": [{"var": "a", "path": "a"}, {"var": "b", "path": "b"}],
            }
        }
    )
    bindings = {"vars.a": "before"}
    with pytest.raises(base.SelectorError, match=r"parseJson: extract entry 2 \(path 'b'\)"):
        _do_action(FakeDriver([]), step, bindings=bindings)
    assert bindings == {"vars.a": "before"}


def test_a_value_read_from_a_secret_is_secret_and_stays_so_downstream() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario(
            {
                "name": "secret",
                "steps": [
                    _step("inner", "a", "${secrets.BLOB}"),
                    # Read from a variable the first step marked, with a plain-looking source.
                    _step("leaf", "b", "${vars.inner}"),
                    _step("plain", "c", '{"c":"public"}'),
                ],
            }
        ),
        clock=FakeClock(),
        bindings={"secrets.BLOB": json.dumps({"a": {"b": "hunter2"}})},
    )
    assert result.ok, result.failure
    assert result.derived_secrets == ['{"b":"hunter2"}', "hunter2"]


def test_a_public_value_written_over_a_secret_variable_clears_the_mark() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario(
            {
                "name": "clear",
                "steps": [
                    _step("v", "a", "${secrets.BLOB}"),
                    _step("v", "a", '{"a":"public"}'),
                    _step("copy", "a", '{"a":"${vars.v}"}'),
                ],
            }
        ),
        clock=FakeClock(),
        bindings={"secrets.BLOB": '{"a":"hunter2"}'},
    )
    assert result.ok, result.failure
    assert result.derived_secrets == ["hunter2"]


def test_a_step_with_no_var_scope_does_nothing() -> None:
    _do_action(FakeDriver([]), Step.model_validate(_step("v", "a", "not json")), bindings=None)


def test_a_value_too_deep_to_serialize_fails_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    def too_deep(_value: object) -> str:
        raise RecursionError

    monkeypatch.setattr(parse_json, "json_text", too_deep)
    with pytest.raises(ParseJsonError, match="nested too deeply"):
        _eval_over(_spec(("v", "a")), {"a": {}})


def test_a_step_that_wrote_nothing_records_no_secret() -> None:
    derived = DerivedSecrets()
    spec = _spec(("v", "a"), source="${secrets.BLOB}")
    record_parse_json(spec, ["v"], {}, derived, completed=False)
    assert derived.values == [] and derived.secret_vars == set()

"""Tests for the `eval` step: load-time checks, every operation, and run-time failures (BE-0472)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator.actions.handlers.eval_ import (
    MAX_CHARS,
    MAX_ITEMS,
    _do_eval,
    evaluate,
    record_eval,
    run_ops,
)
from bajutsu.common.orchestrator.types import DerivedSecrets
from bajutsu.common.scenario import Eval, Step
from bajutsu.common.scenario.models.actions.eval_ops import EvalOpError, parse_ops


def _step(ops: list[object], into: dict[str, object] | None = None, src: str = "x") -> Step:
    return Step.model_validate(
        {"eval": {"from": src, "ops": ops, "into": into if into is not None else {"var": "v"}}}
    )


def _run(source: str, ops: list[object]) -> str | list[str]:
    return run_ops(source, parse_ops(ops))


# --- load time ---


def test_eval_is_an_exclusive_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(
            {"eval": {"from": "x", "ops": ["trim"], "into": {"var": "v"}}, "tap": {"id": "a"}}
        )


def test_every_operation_form_loads() -> None:
    _step([{"split": ","}, {"filter": {"matches": "^a"}}, "sort", "unique", "reverse", "count"])
    _step([{"split": {"by": "\\s+", "regex": True}}, {"filter": {"equals": "a", "not": True}}])
    _step([{"split": ","}, {"filter": {"contains": "a"}}, {"sort": "desc"}, {"join": ""}])
    _step([{"split": ","}, {"filter": {"notEmpty": True}}, {"sort": {"numeric": True}}])
    _step([{"split": ","}, {"pick": "first"}, "trim", {"case": "upper"}])
    _step([{"split": ","}, {"pick": 2}, {"regex": {"pattern": "(a)", "group": 1}}])
    _step(["json", {"pick": "last"}, {"replace": {"find": "a", "with": "b", "regex": True}}])
    _step([{"substring": {"start": 0, "end": 3}}, {"case": "lower"}])


@pytest.mark.parametrize(
    ("ops", "message"),
    [
        ([], "ops must not be empty"),
        (["split"], r"op 1 \(split\): takes arguments"),
        (["frobnicate"], "op 1: unknown operation"),
        ([{"split": ",", "join": "-"}], "op 1: an operation is a name"),
        ([{"trim": True}], r"op 1 \(trim\): takes no arguments"),
        ([{"split": ""}], "the separator must not be empty"),
        ([{"split": {"by": "(", "regex": True}}], "`by` is not a valid regular expression"),
        ([{"split": ","}, {"filter": {}}], "exactly one of"),
        ([{"split": ","}, {"filter": {"equals": "a", "contains": "b"}}], "exactly one of"),
        ([{"split": ","}, {"filter": {"notEmpty": False}}], "`notEmpty` must be true"),
        (
            [{"split": ","}, {"filter": {"equals": "a", "not": "yes"}}],
            "`not` must be true or false",
        ),
        ([{"split": ","}, {"pick": -1}], "non-negative integer"),
        ([{"split": ","}, {"pick": 1.5}], "non-negative integer"),
        ([{"split": ","}, {"pick": True}], "non-negative integer"),
        ([{"split": ","}, {"sort": "up"}], "`order` must be asc or desc"),
        ([{"split": ","}, {"sort": {"order": "asc", "by": "x"}}], "unknown argument"),
        ([{"regex": {"pattern": "["}}], "`pattern` is not a valid regular expression"),
        ([{"regex": {"group": 1}}], "`pattern` is required"),
        ([{"replace": {"find": "a"}}], "`with` is required"),
        ([{"replace": {"find": 1, "with": "b"}}], "`find` must be a string"),
        ([{"substring": {"end": 2}}], "`start` is required"),
        ([{"case": "title"}], "takes lower or upper"),
        ([{"join": "-"}], r"op 1 \(join\): takes a list, but receives a string"),
        ([{"split": ","}, "trim"], r"op 2 \(trim\): takes a string, but receives a list"),
    ],
)
def test_a_load_error_names_the_operation(ops: list[object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _step(ops)


def test_into_takes_exactly_one_of_var_and_vars() -> None:
    intos: list[dict[str, object]] = [
        {},
        {"var": "a", "vars": ["b"]},
        {"vars": []},
        {"vars": ["a", "a"]},
    ]
    for into in intos:
        with pytest.raises(ValidationError):
            _step([{"split": ","}], into)


def test_into_vars_needs_a_list_result() -> None:
    with pytest.raises(ValidationError, match=r"into\.vars needs a list result"):
        _step(["trim"], {"vars": ["a"]})


def test_an_argument_holding_a_token_is_checked_at_run_time_instead() -> None:
    # The loader cannot judge `${...}`, so it accepts the step; the type flow is still checked.
    _step([{"split": ","}, {"pick": "${vars.i}"}, {"regex": {"pattern": "${vars.p}"}}])
    with pytest.raises(ValidationError, match="takes a list"):
        _step([{"pick": "${vars.i}"}])


def test_the_written_step_round_trips_through_a_dump() -> None:
    step = _step([{"split": {"by": ",", "regex": False}}, "sort"], src="${vars.a}")
    assert Step.model_validate(step.model_dump(by_alias=True, exclude_none=True)) == step


# --- operations ---


def test_split_on_a_literal_and_on_a_regex() -> None:
    assert _run("a,b,,c", [{"split": ","}]) == ["a", "b", "", "c"]
    assert _run("Apple, banana ,Cherry", [{"split": {"by": "\\s*,\\s*", "regex": True}}]) == [
        "Apple",
        "banana",
        "Cherry",
    ]


def test_split_on_a_regex_ignores_capture_groups() -> None:
    # `re.split` would splice each captured "-" into the result; the elements are the gaps alone.
    assert _run("a-b-c", [{"split": {"by": "(-)", "regex": True}}]) == ["a", "b", "c"]


def test_join_filter_and_count() -> None:
    assert _run("a,b", [{"split": ","}, {"join": ""}]) == "ab"
    assert _run("ab,b,c", [{"split": ","}, {"filter": {"matches": "b"}}]) == ["ab", "b"]
    assert _run("ab,b,c", [{"split": ","}, {"filter": {"equals": "b", "not": True}}]) == [
        "ab",
        "c",
    ]
    assert _run("ab,b,c", [{"split": ","}, {"filter": {"contains": "a"}}]) == ["ab"]
    assert _run("a,,b", [{"split": ","}, {"filter": {"notEmpty": True}}]) == ["a", "b"]
    assert _run("a,,b", [{"split": ","}, {"filter": {"notEmpty": True, "not": True}}]) == [""]
    assert _run("a,b", [{"split": ","}, {"filter": {"equals": "z"}}, "count"]) == "0"


def test_pick_by_keyword_and_index() -> None:
    assert _run("a,b,c", [{"split": ","}, {"pick": "first"}]) == "a"
    assert _run("a,b,c", [{"split": ","}, {"pick": "last"}]) == "c"
    assert _run("a,b,c", [{"split": ","}, {"pick": 1}]) == "b"
    assert _run("a,b,c", [{"split": ","}, {"pick": "2"}]) == "c"  # a substituted index is text


def test_sort_unique_and_reverse() -> None:
    assert _run("b,a,B", [{"split": ","}, "sort"]) == ["B", "a", "b"]  # code point order
    assert _run("b,a,B", [{"split": ","}, {"sort": "desc"}]) == ["b", "a", "B"]
    assert _run("10,9,-1.5", [{"split": ","}, {"sort": {"numeric": True}}]) == ["-1.5", "9", "10"]
    assert _run("b,a,b,a", [{"split": ","}, "unique"]) == ["b", "a"]
    assert _run("a,b", [{"split": ","}, "reverse"]) == ["b", "a"]


def test_sort_desc_is_sort_then_reverse_even_on_ties() -> None:
    ops: list[object] = [{"split": ","}, {"sort": {"order": "desc", "numeric": True}}]
    assert _run("1,1.0,2", ops) == _run(
        "1,1.0,2", [{"split": ","}, {"sort": {"numeric": True}}, "reverse"]
    )


def test_json_parses_an_array_and_renders_non_strings() -> None:
    assert _run('["a", 1, true, null, {"k": "é"}]', ["json"]) == [
        "a",
        "1",
        "true",
        "null",
        '{"k":"é"}',
    ]


def test_string_operations() -> None:
    assert _run("Order #A-1042 confirmed", [{"regex": {"pattern": "#(\\S+)", "group": 1}}]) == (
        "A-1042"
    )
    assert _run("a.b.c", [{"replace": {"find": ".", "with": "-"}}]) == "a-b-c"
    assert _run("a1b22", [{"replace": {"find": "(\\d+)", "with": "<\\1>", "regex": True}}]) == (
        "a<1>b<22>"
    )
    assert _run("abcdef", [{"substring": {"start": 1, "end": 3}}]) == "bc"
    assert _run("abcdef", [{"substring": {"start": 4}}]) == "ef"
    assert _run("  a b  ", ["trim"]) == "a b"
    assert _run("aB", [{"case": "upper"}]) == "AB"
    assert _run("aB", [{"case": "lower"}]) == "ab"


# --- run-time failures ---


@pytest.mark.parametrize(
    ("source", "ops", "message"),
    [
        ("abc", [{"regex": {"pattern": "z"}}], r"op 1 \(regex\): the pattern does not match"),
        ("abc", [{"regex": {"pattern": "a", "group": 1}}], "the pattern has no group 1"),
        ("abc", [{"regex": {"pattern": "a|(z)", "group": 1}}], "the pattern does not match"),
        ("a,b", [{"split": ","}, {"pick": 2}], r"op 2 \(pick\): the index is outside the list"),
        ("a", [{"split": ","}, {"filter": {"equals": "z"}}, {"pick": "first"}], "outside the list"),
        ("abc", [{"substring": {"start": 4}}], "outside the string"),
        ("abc", [{"substring": {"start": 0, "end": 4}}], "outside the string"),
        ("abc", [{"substring": {"start": 2, "end": 1}}], "outside the string"),
        ("{}", ["json"], "not a JSON array"),
        ("nope", ["json"], "not a JSON array"),
        ("1,x", [{"split": ","}, {"sort": {"numeric": True}}], "not a decimal number"),
        (
            "ab",
            [{"replace": {"find": "a", "with": "\\9", "regex": True}}],
            "replacement is not valid",
        ),
    ],
)
def test_a_run_time_failure_names_the_operation(
    source: str, ops: list[object], message: str
) -> None:
    with pytest.raises(EvalOpError, match=message):
        _run(source, ops)


def test_a_failure_message_never_quotes_a_value() -> None:
    secret = "hunter2-secret"
    cases: list[list[object]] = [
        [{"regex": {"pattern": "zzz"}}],
        [{"split": "-"}, {"pick": 9}],
        ["json"],
        [{"split": "-"}, {"sort": {"numeric": True}}],
    ]
    for ops in cases:
        with pytest.raises(EvalOpError) as exc:
            _run(secret, ops)
        assert "hunter2" not in str(exc.value)


def test_the_caps_bound_an_input_and_a_result() -> None:
    with pytest.raises(EvalOpError, match="from: the string exceeds"):
        _run("a" * (MAX_CHARS + 1), ["trim"])
    # A `replace` that grows a value past the cap fails at that operation, not later.
    with pytest.raises(EvalOpError, match=r"op 1 \(replace\): the string exceeds"):
        _run("a" * (MAX_CHARS // 2 + 1), [{"replace": {"find": "a", "with": "aa"}}])
    with pytest.raises(EvalOpError, match=r"op 1 \(split\): the list exceeds"):
        _run("," * MAX_ITEMS, [{"split": ","}])
    with pytest.raises(EvalOpError, match=r"op 1 \(json\): the list exceeds"):
        _run(json.dumps(["a"] * (MAX_ITEMS + 1)), ["json"])


# --- substitution and the handler ---


def _spec(ops: list[object], into: dict[str, object] | None = None, src: str = "x") -> Eval:
    step = _step(ops, into, src)
    assert step.eval_ is not None
    return step.eval_


def test_a_list_result_is_stored_as_compact_json_text() -> None:
    assert evaluate(_spec([{"split": ","}], src="a,é"), {}) == {"vars.v": '["a","é"]'}


def test_into_vars_spreads_a_list_and_drops_extra_elements() -> None:
    spec = _spec([{"split": " "}], {"vars": ["first", "last"]}, src="Taro Yamada San")
    assert evaluate(spec, {}) == {"vars.first": "Taro", "vars.last": "Yamada"}


def test_into_vars_fails_on_a_list_shorter_than_the_names() -> None:
    spec = _spec([{"split": " "}], {"vars": ["a", "b", "c"]}, src="x y")
    with pytest.raises(EvalOpError, match="names 3 variables, but the list is shorter"):
        evaluate(spec, {})


def test_tokens_in_from_and_arguments_are_substituted() -> None:
    spec = _spec([{"split": "${vars.sep}"}, {"pick": "${vars.i}"}], src="${vars.a}-${vars.b}")
    bindings = {"vars.a": "x", "vars.b": "y", "vars.sep": "-", "vars.i": "1"}
    assert evaluate(spec, bindings) == {"vars.v": "y"}


def test_a_substituted_argument_is_checked_with_the_load_time_rule() -> None:
    spec = _spec([{"split": ","}, {"pick": "${vars.i}"}])
    with pytest.raises(EvalOpError, match=r"op 2 \(pick\): the index must be"):
        evaluate(spec, {"vars.i": "-1"})
    spec = _spec([{"split": "${vars.sep}"}])
    with pytest.raises(EvalOpError, match="the separator must not be empty"):
        evaluate(spec, {"vars.sep": ""})


def test_an_unbound_token_fails_rather_than_running_on_its_literal_text() -> None:
    with pytest.raises(EvalOpError, match=r"no value is bound for \$\{vars.missing\}"):
        evaluate(_spec(["trim"], src="${vars.missing}"), {})


def test_the_handler_writes_vars_and_a_failure_writes_nothing() -> None:
    bindings = {"vars.v": "before"}
    _do_eval(FakeDriver([]), _step(["trim"], src=" a "), None, None, bindings)
    assert bindings["vars.v"] == "a"
    with pytest.raises(base.SelectorError, match=r"^eval: op 1 \(regex\)"):
        _do_eval(FakeDriver([]), _step([{"regex": {"pattern": "z"}}]), None, None, bindings)
    assert bindings["vars.v"] == "a"
    _do_eval(FakeDriver([]), _step(["trim"]), None, None, None)  # no scope: a no-op


# --- secret status ---


def _record(spec: Eval, bindings: dict[str, str], derived: DerivedSecrets) -> str:
    bindings.update(evaluate(spec, bindings))
    produced = record_eval(spec, bindings, derived, completed=True)
    assert produced is not None
    return produced


def test_a_result_from_a_secret_is_registered_and_its_var_marked() -> None:
    derived = DerivedSecrets()
    # Low-entropy on purpose, so the repository's secret scanner reads it as the placeholder it is.
    bindings = {"secrets.CODE": "aaaabbbbcccc"}
    spec = _spec([{"substring": {"start": 0, "end": 8}}], src="${secrets.CODE}")
    assert _record(spec, bindings, derived) == "aaaabbbb"
    assert derived.values == ["aaaabbbb"]
    assert derived.secret_vars == {"v"}


def test_a_result_from_a_secret_var_is_secret_in_turn() -> None:
    derived = DerivedSecrets(secret_vars={"prefix"})
    bindings = {"vars.prefix": "abcd"}
    spec = _spec(["trim"], {"var": "copy"}, src="${vars.prefix}")
    _record(spec, bindings, derived)
    assert derived.values == ["abcd"]
    assert "copy" in derived.secret_vars


def test_a_secret_argument_makes_the_result_secret() -> None:
    derived = DerivedSecrets()
    bindings = {"secrets.SEP": "~"}
    spec = _spec([{"replace": {"find": "-", "with": "${secrets.SEP}"}}], src="a-b")
    _record(spec, bindings, derived)
    assert derived.values == ["a~b"]


def test_a_non_secret_result_clears_the_mark() -> None:
    derived = DerivedSecrets(secret_vars={"v"})
    _record(_spec(["trim"], src="plain"), {}, derived)
    assert derived.secret_vars == set()
    assert derived.values == []


def test_count_never_derives_a_secret() -> None:
    derived = DerivedSecrets()
    bindings = {"secrets.LIST": "a,b,c"}
    spec = _spec([{"split": ","}, "count"], src="${secrets.LIST}")
    assert _record(spec, bindings, derived) == "3"
    assert derived.values == []
    assert derived.secret_vars == set()


def test_an_argument_after_count_can_still_pass_a_secret_on() -> None:
    derived = DerivedSecrets()
    bindings = {"secrets.LIST": "a,b", "secrets.SUFFIX": "!"}
    spec = _spec(
        [{"split": ","}, "count", {"replace": {"find": "2", "with": "2${secrets.SUFFIX}"}}],
        src="${secrets.LIST}",
    )
    _record(spec, bindings, derived)
    assert derived.values == ["2!"]


def test_a_spread_secret_list_registers_every_element() -> None:
    derived = DerivedSecrets()
    bindings = {"secrets.PAIR": "user:pass"}
    spec = _spec([{"split": ":"}], {"vars": ["u", "p"]}, src="${secrets.PAIR}")
    assert _record(spec, bindings, derived) == '["user","pass"]'
    assert derived.values == ["user", "pass"]
    assert derived.secret_vars == {"u", "p"}


def test_registering_notifies_listeners_once_per_new_value() -> None:
    seen: list[list[str]] = []
    derived = DerivedSecrets(listeners=[lambda vs: seen.append(list(vs))])
    derived.register(["a", "a", "", "b"])
    derived.register(["a"])
    assert seen == [["a", "b"]]


def test_masking_a_result_reaches_every_value_bearing_field_and_no_path() -> None:
    from bajutsu.common.assertions import AssertionResult
    from bajutsu.common.evidence import Artifact
    from bajutsu.common.evidence.redaction import Redactor
    from bajutsu.common.orchestrator.types import (
        AlertEvent,
        RunResult,
        StepOutcome,
        mask_derived_text,
    )

    code = "481516"
    outcome = StepOutcome(
        index=0,
        action="assert",
        reason=f"expected {code}",
        generated=code,
        assertion_results=[AssertionResult(True, "value", detail=code, reason=code)],
        alerts=[AlertEvent(label=f"Your code is {code}")],
        artifacts=[Artifact(f"00-s/{code}/elements.json", "elements", "fake")],
    )
    result = RunResult(
        scenario="s",
        ok=False,
        steps=[outcome],
        failure=f"step 0: {code}",
        expect_alerts=[AlertEvent(label=code)],
    )
    mask_derived_text(result, Redactor(None).with_values([code]))
    text = repr(result)
    assert text.count(code) == 1  # only the artifact path, which the report links by
    assert result.steps[0].artifacts[0].name == f"00-s/{code}/elements.json"


def test_a_step_failed_after_its_write_still_registers_but_never_clears() -> None:
    # A later check (an `extract`, an interruption) can fail the step after the handler wrote; the
    # secret value sits in `bindings` either way, so it must still join the masked set.
    derived = DerivedSecrets()
    bindings = {"secrets.CODE": "aaaabbbbcccc", "vars.v": "aaaabbbb"}
    secret = _spec([{"substring": {"start": 0, "end": 8}}], src="${secrets.CODE}")
    record_eval(secret, bindings, derived, completed=False)
    assert derived.values == ["aaaabbbb"]
    assert derived.secret_vars == {"v"}
    # A non-secret step that did not complete leaves the earlier mark: the variable may still hold
    # the old secret value.
    record_eval(_spec(["trim"], src="plain"), bindings, derived, completed=False)
    assert derived.secret_vars == {"v"}


def test_nothing_is_recorded_for_a_variable_that_holds_no_value() -> None:
    derived = DerivedSecrets()
    secret = _spec(["trim"], src="${secrets.CODE}")
    assert record_eval(secret, {}, derived, completed=False) is None
    assert derived.values == []

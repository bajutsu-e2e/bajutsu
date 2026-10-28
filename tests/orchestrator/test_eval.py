"""Tests for the `eval` step during a run: vars.*, the recorded value, and derived secrets (BE-0472)."""

from __future__ import annotations

from collections.abc import Sequence

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.orchestrator import run_scenario


class _RecordingSink(NullSink):
    """A sink that captures nothing but records each batch of values it is told to mask."""

    def __init__(self) -> None:
        self.masked: list[list[str]] = []

    def add_secret_values(self, values: Sequence[str]) -> None:
        self.masked.append(list(values))


def test_capture_split_filter_sort_pick_and_assert() -> None:
    # The flow BE-0472's Motivation describes: one captured label, split into sorted items, the
    # first of which a later step asserts — no helper script, no scratch service.
    driver = FakeDriver(
        [el("cart.summary", "Apple, banana ,Cherry"), el("search", "Search", value="Apple")]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "eval flow",
                "steps": [
                    {
                        "tap": {"id": "cart.summary"},
                        "extract": {"items": {"sel": {"id": "cart.summary"}, "prop": "label"}},
                    },
                    {
                        "eval": {
                            "from": "${vars.items}",
                            "ops": [
                                {"split": {"by": "\\s*,\\s*", "regex": True}},
                                {"filter": {"notEmpty": True}},
                                "sort",
                            ],
                            "into": {"var": "sortedItems"},
                        }
                    },
                    {
                        "eval": {
                            "from": "${vars.sortedItems}",
                            "ops": ["json", {"pick": "first"}],
                            "into": {"var": "firstItem"},
                        }
                    },
                    {
                        "assert": [
                            {"value": {"sel": {"id": "search"}, "equals": "${vars.firstItem}"}}
                        ]
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert result.steps[1].generated == '["Apple","Cherry","banana"]'
    assert result.steps[2].generated == "Apple"
    assert result.steps[0].generated is None


def test_into_vars_spreads_the_split_elements_over_separate_variables() -> None:
    driver = FakeDriver([el("first", "First", value="Taro"), el("last", "Last", value="Yamada")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "spread",
                "steps": [
                    {
                        "eval": {
                            "from": "Taro Yamada",
                            "ops": [{"split": " "}],
                            "into": {"vars": ["given", "family"]},
                        }
                    },
                    {
                        "assert": [
                            {"value": {"sel": {"id": "first"}, "equals": "${vars.given}"}},
                            {"value": {"sel": {"id": "last"}, "equals": "${vars.family}"}},
                        ]
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert result.steps[0].generated == '["Taro","Yamada"]'


def test_a_failing_pipeline_fails_the_step_and_leaves_the_variable_unset() -> None:
    driver = FakeDriver([el("field", "Field")])
    bindings: dict[str, str] = {}
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "no match",
                "steps": [
                    {
                        "eval": {
                            "from": "Order confirmed",
                            "ops": [{"regex": {"pattern": "#(\\S+)", "group": 1}}],
                            "into": {"var": "orderId"},
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
        bindings=bindings,
    )
    assert not result.ok
    assert "eval: op 1 (regex): the pattern does not match" in (result.failure or "")
    assert "vars.orderId" not in bindings
    assert result.steps[0].generated is None


def test_a_secret_derived_value_reaches_the_sink_and_the_result() -> None:
    sink = _RecordingSink()
    result = run_scenario(
        FakeDriver([el("field", "Field")]),
        _scenario(
            {
                "name": "secret prefix",
                "steps": [
                    {
                        "eval": {
                            "from": "${secrets.API_TOKEN}",
                            "ops": [{"substring": {"start": 0, "end": 8}}],
                            "into": {"var": "tokenPrefix"},
                        }
                    },
                    {
                        "eval": {
                            "from": "${vars.tokenPrefix}",
                            "ops": [{"case": "upper"}],
                            "into": {"var": "loud"},
                        }
                    },
                    {
                        "eval": {
                            "from": "${secrets.API_TOKEN}",
                            "ops": [{"split": "-"}, "count"],
                            "into": {"var": "parts"},
                        }
                    },
                ],
            }
        ),
        clock=FakeClock(),
        sink=sink,
        bindings={"secrets.API_TOKEN": "abcdefgh-ijkl-mnop"},
    )
    assert result.ok, result.failure
    # The prefix, and the value derived from the variable holding it, both join the masked set; the
    # count over the secret does not.
    assert sink.masked == [["abcdefgh"], ["ABCDEFGH"]]
    assert result.derived_secrets == ["abcdefgh", "ABCDEFGH"]
    assert result.steps[2].generated == "3"


def test_a_variable_marked_secret_in_steps_stays_secret_in_cleanup() -> None:
    sink = _RecordingSink()
    result = run_scenario(
        FakeDriver([el("field", "Field")]),
        _scenario(
            {
                "name": "cleanup reads a secret var",
                "steps": [
                    {
                        "eval": {
                            "from": "${secrets.PW}",
                            "ops": ["trim"],
                            "into": {"var": "pw"},
                        }
                    }
                ],
                "cleanup": [
                    {
                        "on": "always",
                        "steps": [
                            {
                                "eval": {
                                    "from": "${vars.pw}",
                                    "ops": [{"case": "upper"}],
                                    "into": {"var": "loudPw"},
                                }
                            }
                        ],
                    }
                ],
            }
        ),
        clock=FakeClock(),
        sink=sink,
        bindings={"secrets.PW": "hunter2"},
    )
    assert result.ok, result.failure
    assert result.derived_secrets == ["hunter2", "HUNTER2"]


def _secret_eval(var: str) -> dict[str, object]:
    return {"eval": {"from": "${secrets.PW}", "ops": [{"case": "upper"}], "into": {"var": var}}}


def test_an_eval_inside_if_and_for_each_registers_on_the_same_scenario() -> None:
    # Nested branches run through the scenario's one step loop, so a value they derive joins the
    # same masked set as a top-level one (BE-0472).
    sink = _RecordingSink()
    driver = FakeDriver([el("row.1", "Row 1"), el("row.2", "Row 2")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "nested eval",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "row.1"}},
                            "then": [_secret_eval("inIf")],
                        }
                    },
                    {
                        "forEach": {
                            "sel": {"idMatches": "row.*"},
                            "as": "row",
                            "steps": [
                                {
                                    "eval": {
                                        "from": "${secrets.PW}-${vars.row}",
                                        "ops": ["trim"],
                                        "into": {"var": "inLoop"},
                                    }
                                }
                            ],
                        }
                    },
                ],
            }
        ),
        clock=FakeClock(),
        sink=sink,
        bindings={"secrets.PW": "hunter2"},
    )
    assert result.ok, result.failure
    assert result.derived_secrets == ["HUNTER2", "hunter2-row.1", "hunter2-row.2"]
    assert sink.masked == [["HUNTER2"], ["hunter2-row.1"], ["hunter2-row.2"]]


def test_every_targets_sink_learns_a_derived_secret() -> None:
    from bajutsu.common.orchestrator.types import TargetRuntime

    primary, other = _RecordingSink(), _RecordingSink()
    app, web = FakeDriver([el("a", "A")]), FakeDriver([el("w", "W")])
    result = run_scenario(
        app,
        _scenario(
            {
                "name": "two targets",
                "targets": ["app", "web"],
                "steps": [{"target": "app", **_secret_eval("v")}],
            }
        ),
        clock=FakeClock(),
        sink=primary,
        target_runtimes={
            "app": TargetRuntime(driver=app, sink=primary),
            "web": TargetRuntime(driver=web, sink=other),
        },
        primary_target="app",
        bindings={"secrets.PW": "hunter2"},
    )
    assert result.ok, result.failure
    assert ["HUNTER2"] in primary.masked
    assert other.masked == [["HUNTER2"]]


def test_a_sink_leased_after_an_earlier_attempt_starts_out_masking_its_values() -> None:
    # A crash retry runs the scenario again on a fresh lease; the runner hands both attempts one
    # `DerivedSecrets`, so the new sink masks what the first attempt already derived.
    from bajutsu.common.orchestrator.types import DerivedSecrets

    derived = DerivedSecrets(values=["from-attempt-1"])
    sink = _RecordingSink()
    run_scenario(
        FakeDriver([el("a", "A")]),
        _scenario({"name": "retry", "steps": [{"tap": {"id": "a"}}]}),
        clock=FakeClock(),
        sink=sink,
        derived_secrets=derived,
    )
    assert sink.masked == [["from-attempt-1"]]


def test_a_substituted_argument_that_breaks_a_rule_fails_without_quoting_the_secret() -> None:
    result = run_scenario(
        FakeDriver([el("a", "A")]),
        _scenario(
            {
                "name": "bad secret pattern",
                "steps": [
                    {
                        "eval": {
                            "from": "x",
                            "ops": [{"regex": {"pattern": "${secrets.PATTERN}"}}],
                            "into": {"var": "v"},
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
        bindings={"secrets.PATTERN": "(unclosed-secret"},
    )
    assert not result.ok
    assert "op 1 (regex): `pattern` is not a valid regular expression" in (result.failure or "")
    assert "unclosed-secret" not in (result.failure or "")

"""Tests for the orchestrator run loop.

Use FakeDriver (in-memory backend) and FakeClock (sleep advances time) to test
act -> wait -> verify deterministically without a Simulator.
"""

from __future__ import annotations

from collections.abc import Callable

from conftest import el

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Relaunch, Scenario


class FakeClock:
    """Advance logical time on sleep; `on_sleep` mutates the world over time."""

    def __init__(self, on_sleep: Callable[[float], None] | None = None) -> None:
        self._t = 0.0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds
        if self.on_sleep is not None:
            self.on_sleep(self._t)


def _scenario(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


def test_happy_path_tap_and_expect() -> None:
    driver = FakeDriver([el("home.title", "ホーム"), el("settings.open", "設定", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "open settings",
                "steps": [{"tap": {"id": "settings.open"}}],
                "expect": [{"exists": {"id": "home.title"}}],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok
    assert driver.actions == [("tap", {"id": "settings.open"})]


def test_progress_reports_each_step() -> None:
    """`progress` receives one line per step, labeled by the step name or action + target id."""
    driver = FakeDriver([el("counter.inc", "+", ["button"]), el("counter.val", "0")])
    lines: list[str] = []
    run_scenario(
        driver,
        _scenario(
            {
                "name": "count up",
                "steps": [
                    {"tap": {"id": "counter.inc"}},  # no name → "tap counter.inc"
                    {"name": "check it", "assert": [{"exists": {"id": "counter.val"}}]},
                ],
            }
        ),
        clock=FakeClock(),
        scenario_id="00-count-up",
        progress=lines.append,
    )
    assert lines == [
        "00-count-up · step 1: tap counter.inc",
        "00-count-up · step 2: check it",  # the step's own name wins over the action label
    ]


def test_progress_defaults_to_silent() -> None:
    driver = FakeDriver([el("home.title", "H")])
    # No progress callback → no error, runs as before.
    res = run_scenario(
        driver,
        _scenario({"name": "n", "steps": [{"tap": {"id": "home.title"}}]}),
        clock=FakeClock(),
    )
    assert res.ok


def test_run_scenario_records_duration() -> None:
    # The result carries the scenario's wall-clock (measured off the injected clock) so the
    # report can show per-scenario and total execution time.
    here = el("here", "H")
    driver = FakeDriver([])  # 'here' shows only after the first poll-sleep advances the clock

    def appear(_t: float) -> None:
        driver.screen = [here]

    scn = _scenario({"name": "d", "steps": [{"wait": {"for": {"id": "here"}, "timeout": 1}}]})
    result = run_scenario(driver, scn, clock=FakeClock(appear))
    assert result.ok
    assert result.duration_s == 0.05  # exactly one 0.05s poll elapsed


def test_relaunch_invokes_injected_callback() -> None:
    # A relaunch step calls the injected relauncher with its env/args overrides.
    seen: list[Relaunch] = []
    scn = _scenario(
        {"name": "r", "steps": [{"relaunch": {"env": {"SEED": "9"}, "args": ["--fresh"]}}]}
    )
    res = run_scenario(FakeDriver([el("home.title", "H")]), scn, relaunch=seen.append)
    assert res.ok, res.failure
    assert len(seen) == 1 and seen[0].env == {"SEED": "9"} and seen[0].args == ["--fresh"]


def test_relaunch_without_callback_fails_cleanly() -> None:
    # No relauncher injected (e.g. fake driver) -> a clear failure, not a crash.
    scn = _scenario({"name": "r", "steps": [{"relaunch": {}}]})
    res = run_scenario(FakeDriver([el("home.title", "H")]), scn)
    assert not res.ok and "relaunch" in (res.failure or "")


def test_react_transition_then_expect() -> None:
    home = [el("settings.open", "設定", ["button"])]
    settings = [el("settings.reindex", "再生成", ["button"]), el("settings.title", "設定")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"id": "settings.open"}:
            d.screen = settings

    driver = FakeDriver(home, react=react)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "drill into settings",
                "steps": [{"tap": {"id": "settings.open"}}, {"tap": {"id": "settings.reindex"}}],
                "expect": [{"exists": {"id": "settings.title"}}],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok


def test_tap_not_found_fails_and_stops() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "missing"}}, {"tap": {"id": "a"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert result.failure is not None and "step 0" in result.failure
    assert len(result.steps) == 1  # stops after the failing step


def test_tap_ambiguous_fails() -> None:
    driver = FakeDriver([el("row.1", "A", ["cell"]), el("row.2", "B", ["cell"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"idMatches": "row.*"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "件一致" in result.steps[0].reason  # ambiguous


def test_assert_step_intermediate() -> None:
    driver = FakeDriver([el("counter", "c", ["staticText"], value="3")])
    ok = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"assert": [{"value": {"sel": {"id": "counter"}, "equals": "3"}}]}],
            }
        ),
        clock=FakeClock(),
    )
    assert ok.ok
    bad = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"assert": [{"value": {"sel": {"id": "counter"}, "equals": "4"}}]}],
            }
        ),
        clock=FakeClock(),
    )
    assert not bad.ok
    assert bad.steps[0].assertion_results[0].ok is False


def test_wait_for_appears() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1 and all(e["identifier"] != "ready" for e in driver.screen):
            driver.screen = [*driver.screen, el("ready", "R")]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_timeout() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "timeout" in result.steps[0].reason


def test_wait_until_gone() -> None:
    driver = FakeDriver([el("spinner", "")])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = []

    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"wait": {"until": {"gone": {"id": "spinner"}}, "timeout": 1.0}}],
            }
        ),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_screen_changed() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = [el("b", "B", ["button"])]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "screenChanged", "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_screen_changed_times_out_when_screen_is_static() -> None:
    # A screen that never changes must fail the step, not pass it — a wrongly-passing
    # wait would silently weaken every downstream assertion.
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "screenChanged", "timeout": 0.2}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "timeout: screenChanged" in result.steps[0].reason


def test_wait_settled_waits_for_a_stable_screen() -> None:
    driver = FakeDriver([el("home", "Home", ["button"])])

    def on_sleep(t: float) -> None:
        if t < 0.15:  # a transition still in progress: the frame keeps moving
            driver.screen = [
                {
                    "identifier": "home",
                    "label": "Home",
                    "traits": ["button"],
                    "value": None,
                    "frame": (t, 0.0, 10.0, 10.0),
                }
            ]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 2.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok and result.steps[0].ok


def test_wait_settled_proceeds_on_blank_screen() -> None:
    driver = FakeDriver([])  # collapsed / covered: never settles, but must not fail the step

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 0.3}}]}),
        clock=FakeClock(),
    )
    assert result.ok and result.steps[0].ok


def test_type_and_swipe_actions() -> None:
    driver = FakeDriver([el("search.field", "検索", ["textField"]), el("list", "", ["table"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"type": {"text": "hello", "into": {"id": "search.field"}}},
                    {"swipe": {"on": {"id": "list"}, "direction": "up"}},
                    {"swipe": {"from": [1, 2], "to": [3, 4]}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok
    assert [a[0] for a in driver.actions] == ["tap", "type", "swipe", "swipe"]


def test_wait_skips_sleep_when_query_exceeds_poll_interval() -> None:
    """When query() takes longer than _POLL, additional sleep is skipped."""
    from bajutsu.orchestrator import _POLL, _wait
    from bajutsu.scenario import Wait

    sleeps: list[float] = []

    class TrackingClock:
        def __init__(self) -> None:
            self._t = 0.0

        def now(self) -> float:
            return self._t

        def sleep(self, s: float) -> None:
            sleeps.append(s)
            self._t += s

    clock = TrackingClock()
    query_latency = _POLL * 3  # query takes 3x _POLL
    query_count = 0

    class SlowQueryDriver:
        name = "slow"

        def query(self) -> list[base.Element]:
            nonlocal query_count
            clock._t += query_latency  # simulate query latency on the clock
            query_count += 1
            if query_count >= 3:
                return [el("target", "T")]
            return []

    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 5.0})
    ok, reason = _wait(SlowQueryDriver(), w, clock)  # type: ignore[arg-type]
    assert ok
    assert reason == ""
    # query cost > _POLL -> no extra sleep needed
    assert all(s < 0.01 for s in sleeps), f"expected near-zero sleeps, got {sleeps}"


def test_wait_still_sleeps_when_query_is_fast() -> None:
    """When query() is fast, sleep remains at _POLL as before."""
    from bajutsu.orchestrator import _POLL, _wait
    from bajutsu.scenario import Wait

    sleeps: list[float] = []

    class TrackingClock:
        def __init__(self) -> None:
            self._t = 0.0

        def now(self) -> float:
            return self._t

        def sleep(self, s: float) -> None:
            sleeps.append(s)
            self._t += s

    clock = TrackingClock()
    query_count = 0

    class FastQueryDriver:
        name = "fast"

        def query(self) -> list[base.Element]:
            nonlocal query_count
            # query is instant (does not advance clock)
            query_count += 1
            if query_count >= 3:
                return [el("target", "T")]
            return []

    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 5.0})
    ok, _reason = _wait(FastQueryDriver(), w, clock)  # type: ignore[arg-type]
    assert ok
    # query is instant -> sleep stays at _POLL
    assert all(abs(s - _POLL) < 0.001 for s in sleeps), f"expected {_POLL}s sleeps, got {sleeps}"


def test_extract_captures_value_into_vars() -> None:
    """extract captures a UI value and makes it available as ${vars.*} for subsequent steps."""
    driver = FakeDriver(
        [
            el("counter.inc", "+", ["button"]),
            el("counter.value", "Count", value="42"),
        ]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract test",
                "steps": [
                    {
                        "tap": {"id": "counter.inc"},
                        "extract": {"count": {"sel": {"id": "counter.value"}}},
                    },
                    {
                        "assert": [
                            {"value": {"sel": {"id": "counter.value"}, "equals": "${vars.count}"}}
                        ]
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


def test_extract_label_prop() -> None:
    driver = FakeDriver([el("title", "Hello World", ["staticText"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract label",
                "steps": [
                    {
                        "tap": {"id": "title"},
                        "extract": {"heading": {"sel": {"id": "title"}, "prop": "label"}},
                    },
                    {"assert": [{"label": {"sel": {"id": "title"}, "equals": "${vars.heading}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


def test_extract_fails_when_selector_not_found() -> None:
    driver = FakeDriver([el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract missing",
                "steps": [{"tap": {"id": "ok"}, "extract": {"x": {"sel": {"id": "nonexistent"}}}}],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "extract" in (result.failure or "").lower()


def test_extract_skipped_on_failed_step() -> None:
    driver = FakeDriver([el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract on fail",
                "steps": [{"tap": {"id": "missing"}, "extract": {"x": {"sel": {"id": "ok"}}}}],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "missing" in (result.failure or "")


def test_extract_value_used_in_type_step() -> None:
    driver = FakeDriver(
        [el("source", "Source", value="hello"), el("target", "Target", ["textField"])]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract into type",
                "steps": [
                    {"tap": {"id": "source"}, "extract": {"msg": {"sel": {"id": "source"}}}},
                    {"type": {"text": "${vars.msg}", "into": {"id": "target"}}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("type", "hello") in driver.actions


def test_extract_in_scenario_expect() -> None:
    driver = FakeDriver([el("counter", "Count", value="7"), el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract in expect",
                "steps": [{"tap": {"id": "ok"}, "extract": {"n": {"sel": {"id": "counter"}}}}],
                "expect": [{"value": {"sel": {"id": "counter"}, "equals": "${vars.n}"}}],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


def test_extract_selector_is_interpolated() -> None:
    """Tokens in extract selectors are substituted via bindings."""
    driver = FakeDriver([el("ok", "OK", ["button"]), el("target", "T", value="99")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "interp extract",
                "steps": [
                    {
                        "tap": {"id": "ok"},
                        "extract": {"val": {"sel": {"id": "${secrets.sel}"}}},
                    },
                    {"assert": [{"value": {"sel": {"id": "target"}, "equals": "${vars.val}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
        bindings={"secrets.sel": "target"},
    )
    assert result.ok, result.failure


def test_if_takes_then_branch() -> None:
    driver = FakeDriver([el("flag", "F", value="on"), el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if then",
                "steps": [
                    {
                        "if": {
                            "condition": {"value": {"sel": {"id": "flag"}, "equals": "on"}},
                            "then": [{"tap": {"id": "ok"}}],
                            "else": [{"tap": {"id": "missing"}}],
                        },
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "ok"}) in driver.actions


def test_if_takes_else_branch() -> None:
    driver = FakeDriver([el("flag", "F", value="off"), el("fallback", "FB", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if else",
                "steps": [
                    {
                        "if": {
                            "condition": {"value": {"sel": {"id": "flag"}, "equals": "on"}},
                            "then": [{"tap": {"id": "missing"}}],
                            "else": [{"tap": {"id": "fallback"}}],
                        },
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "fallback"}) in driver.actions


def test_if_without_else_skips() -> None:
    driver = FakeDriver([el("flag", "F", value="off")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if no else",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "nonexistent"}},
                            "then": [{"tap": {"id": "missing"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert driver.actions == []


def test_if_condition_is_interpolated() -> None:
    """${vars.*} tokens in if conditions are substituted."""
    driver = FakeDriver([el("x", "X", value="hello"), el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if interp",
                "steps": [
                    {"tap": {"id": "x"}, "extract": {"val": {"sel": {"id": "x"}}}},
                    {
                        "if": {
                            "condition": {"value": {"sel": {"id": "x"}, "equals": "${vars.val}"}},
                            "then": [{"tap": {"id": "ok"}}],
                        }
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "ok"}) in driver.actions


def test_foreach_iterates() -> None:
    driver = FakeDriver(
        [el("item.a", "A", ["button"]), el("item.b", "B", ["button"]), el("other", "X")]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "forEach",
                "steps": [
                    {
                        "forEach": {
                            "sel": {"idMatches": "item.*"},
                            "as": "current",
                            "steps": [{"tap": {"id": "${vars.current}"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    tapped = [a[1] for a in driver.actions if a[0] == "tap"]
    assert tapped == [{"id": "item.a"}, {"id": "item.b"}]


def test_foreach_empty_succeeds() -> None:
    driver = FakeDriver([el("other", "X")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "forEach empty",
                "steps": [
                    {
                        "forEach": {
                            "sel": {"idMatches": "item.*"},
                            "as": "x",
                            "steps": [{"tap": {"id": "${vars.x}"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert driver.actions == []


def test_foreach_no_identifier_fails() -> None:
    driver = FakeDriver(
        [{"identifier": None, "label": "L", "traits": [], "value": None, "frame": (0, 0, 10, 10)}]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "forEach no id",
                "steps": [
                    {
                        "forEach": {
                            "sel": {"label": "L"},
                            "as": "x",
                            "steps": [{"tap": {"id": "${vars.x}"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "identifier" in (result.failure or "").lower()


def test_nested_step_indices_are_unique() -> None:
    """Steps inside if/forEach get monotonically increasing indices, not duplicates."""
    driver = FakeDriver(
        [el("flag", "F", value="on"), el("a", "A", ["button"]), el("b", "B", ["button"])]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "unique idx",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "flag"}},
                            "then": [{"tap": {"id": "a"}}],
                        }
                    },
                    {"tap": {"id": "b"}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    indices = [s.index for s in result.steps]
    assert len(indices) == len(set(indices)), f"duplicate indices: {indices}"


def test_expect_failure() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"tap": {"id": "a"}}],
                "expect": [{"exists": {"id": "missing"}}],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert result.failure is not None and result.failure.startswith("expect:")

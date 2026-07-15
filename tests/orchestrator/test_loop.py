"""Tests for the orchestrator run loop and one-shot actions (FakeDriver + FakeClock)."""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Relaunch


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
    # The directional form is a scroll (BE-0227), the coordinate form a raw drag.
    assert [a[0] for a in driver.actions] == ["tap", "type", "scroll", "swipe"]


def test_step_level_assert_drops_visual_context() -> None:
    """A step-level `assert` never runs the `visual` / `responseSchema` kinds: no per-step
    screenshot is taken, so those inputs are dropped there even when the run carries a visual
    context (they run only at scenario `expect`). Locks the intentional asymmetry (BE-0250 Unit 2).
    """
    from pathlib import Path

    from bajutsu.assertions import EvalContext, VisualContext

    # A context whose screenshot/baseline paths do not exist: were it forwarded, `_eval_visual`
    # would fail with "baseline not found"; dropped, it fails with "no visual context" instead.
    vc = VisualContext(
        screenshot_path=Path("/nonexistent/shot.png"),
        baselines_dir=Path("/nonexistent/baselines"),
        diff_dir=Path("/nonexistent/diff"),
        run_dir=Path("/nonexistent"),
    )
    result = run_scenario(
        FakeDriver([el("home.title", "ホーム")]),
        _scenario(
            {
                "name": "step visual",
                "steps": [{"assert": [{"visual": {"baseline": "home.png"}}]}],
            }
        ),
        clock=FakeClock(),
        ctx=EvalContext(visual=vc),
    )
    assert not result.ok
    assert result.failure is not None and "no visual context" in result.failure

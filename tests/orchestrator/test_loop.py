"""Tests for the orchestrator run loop and one-shot actions (FakeDriver + FakeClock)."""

from __future__ import annotations

import json
from pathlib import Path

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import FileSink
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Relaunch


class _QueryLoggingDriver(FakeDriver):
    """A `FakeDriver` that also logs `query()` into `actions`, so a test can order it against
    `screenshot()` (already logged) and the step's own action (BE-XXXX)."""

    def query(self) -> list[base.Element]:
        self.actions.append(("query", None))
        return super().query()


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


def test_tap_ambiguous_reason_states_the_match_count() -> None:
    # The failure reason names *how many* elements matched, not just "ambiguous" — an author needs
    # the count to know a selector is too broad. Three matches → "3 件一致", and the run stops on it
    # (a single action never taps "whatever matched first" — prime directive 2).
    driver = FakeDriver(
        [el("row.1", "A", ["cell"]), el("row.2", "B", ["cell"]), el("row.3", "C", ["cell"])]
    )
    result = run_scenario(
        driver,
        _scenario(
            {"name": "x", "steps": [{"tap": {"idMatches": "row.*"}}, {"tap": {"id": "row.1"}}]}
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    reason = result.steps[0].reason
    assert (
        reason is not None and "3 件一致" in reason
    )  # the exact match count, not a generic message
    assert len(result.steps) == 1  # stopped on the ambiguous step; the second tap never ran
    assert driver.actions == []  # nothing was tapped


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


def test_step_level_assert_drops_schema_context() -> None:
    """Sibling guard to the visual drop: a step-level `responseSchema` assert is context-less too,
    so a run carrying a schema context does not forward it to step asserts (BE-0250 Unit 2). Were it
    forwarded, the empty timeline would fail with "no matching exchange"; dropped, it fails earlier
    with "no schema context" — so this pins the `schema=None` half of the drop, not just `visual`.
    """
    from pathlib import Path

    from bajutsu.assertions import EvalContext, SchemaContext

    result = run_scenario(
        FakeDriver([el("home.title", "ホーム")]),
        _scenario(
            {
                "name": "step schema",
                "steps": [
                    {
                        "assert": [
                            {"responseSchema": {"schema": "x.json", "request": {"path": "/api"}}}
                        ]
                    }
                ],
            }
        ),
        clock=FakeClock(),
        ctx=EvalContext(schema=SchemaContext(schemas_dir=Path("/nonexistent/schemas"))),
    )
    assert not result.ok
    assert result.failure is not None and "no schema context" in result.failure


# --- pre-step report evidence capture (BE-XXXX) --------------------------------------------------


def test_pre_step_capture_precedes_a_mutating_action(tmp_path: Path) -> None:
    """The report's screenshot for a `tap` step is taken before the tap runs, not after."""
    driver = FakeDriver([el("go", "Go", ["button"]), el("next", "Next", ["button"])])
    run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "go"}}, {"tap": {"id": "next"}}]}),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
    )
    first_screenshot = next(i for i, (kind, _) in enumerate(driver.actions) if kind == "screenshot")
    first_tap = next(i for i, (kind, _) in enumerate(driver.actions) if kind == "tap")
    assert first_screenshot < first_tap


def test_pre_step_capture_precedes_a_non_mutating_step(tmp_path: Path) -> None:
    """The report's screenshot for an `assert`/`wait` step is taken before it reads the tree to
    evaluate itself — not just before a mutating action. `capture()`'s own token order always
    writes the screenshot before it (if needed) queries the tree for `elements.json`
    (`screenshot.before` precedes `elements` in the pre-step call), and the whole call runs before
    `_run_step_body`, so the first screenshot logged precedes the first tree query logged either
    way — whether that query came from the capture's own fallback or the assertion's own read.
    """
    driver = _QueryLoggingDriver([el("home.title", "ホーム")])
    run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"assert": [{"exists": {"id": "home.title"}}]},
                    {"wait": {"for": {"id": "home.title"}, "timeout": 1}},
                ],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
    )
    first_screenshot = next(i for i, (kind, _) in enumerate(driver.actions) if kind == "screenshot")
    first_query = next(i for i, (kind, _) in enumerate(driver.actions) if kind == "query")
    assert first_screenshot < first_query


def test_last_step_gets_a_final_capture_earlier_steps_do_not(tmp_path: Path) -> None:
    """Only the scenario's last step gets a post-step baseline too — every step already gets the
    pre-step one, but only the last has no following step to carry its result forward (BE-XXXX)."""
    driver = FakeDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    run_dir = tmp_path / "run1"
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}}]}),
        clock=FakeClock(),
        sink=FileSink(run_dir),
    )
    assert result.ok
    step0_kinds = {a.kind for a in result.steps[0].artifacts}
    step1_kinds = {a.kind for a in result.steps[1].artifacts}
    step0_names = {a.name for a in result.steps[0].artifacts}
    step1_names = {a.name for a in result.steps[1].artifacts}
    assert step0_kinds == {"screenshot", "elements"}
    assert step1_kinds == {"screenshot", "elements"}
    # Only the first step's screenshot is the pre-step one; the last step's is the final one.
    assert any(name.endswith("before.png") for name in step0_names)
    assert any(name.endswith("after.png") for name in step1_names)
    assert not any(name.endswith("after.png") for name in step0_names)


def test_final_capture_lands_on_the_last_leaf_step_inside_an_if(tmp_path: Path) -> None:
    """A scenario ending in an `if` still gets its final capture on the last *leaf* step actually
    run, not on the `if` container's own (artifact-less) outcome (BE-XXXX)."""
    driver = FakeDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"tap": {"id": "a"}},
                    {
                        "if": {
                            "condition": {"exists": {"id": "b"}},
                            "then": [{"tap": {"id": "b"}}],
                        }
                    },
                ],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
    )
    assert result.ok
    # The `if` container's own outcome carries no capture artifacts; the nested `tap` it ran does.
    if_outcome = next(s for s in result.steps if s.action == "if_")
    leaf_outcome = next(s for s in result.steps if s.action == "tap" and s.index != 0)
    assert if_outcome.artifacts == []
    leaf_names = {a.name for a in leaf_outcome.artifacts}
    assert any(name.endswith("before.png") for name in leaf_names)
    assert any(name.endswith("after.png") for name in leaf_names)


def test_final_capture_lands_on_the_last_leaf_step_inside_a_for_each(tmp_path: Path) -> None:
    """A scenario ending in a `forEach` with matches still gets its final capture on the last
    iteration's last leaf step, not the `forEach` container's own (artifact-less) outcome, and not
    an earlier iteration's step (BE-XXXX)."""
    driver = FakeDriver(
        [
            el("a", "A", ["button"]),
            el("item.1", "Item 1", ["cell"]),
            el("item.2", "Item 2", ["cell"]),
        ]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"tap": {"id": "a"}},
                    {
                        "forEach": {
                            "sel": {"idMatches": "item.*"},
                            "as": "current",
                            "steps": [{"tap": {"id": "${vars.current}"}}],
                        }
                    },
                ],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
    )
    assert result.ok, result.failure
    for_each_outcome = next(s for s in result.steps if s.action == "for_each")
    assert for_each_outcome.artifacts == []
    iteration_taps = [s for s in result.steps if s.action == "tap" and s.index != 0]
    assert len(iteration_taps) == 2  # both items matched
    first_iter_names = {a.name for a in iteration_taps[0].artifacts}
    last_iter_names = {a.name for a in iteration_taps[-1].artifacts}
    # Only the last iteration's tap gets the final capture; an earlier iteration gets only its
    # own pre-step baseline.
    assert any(name.endswith("before.png") for name in first_iter_names)
    assert not any(name.endswith("after.png") for name in first_iter_names)
    assert any(name.endswith("before.png") for name in last_iter_names)
    assert any(name.endswith("after.png") for name in last_iter_names)


def test_final_capture_lands_on_the_last_leaf_step_before_a_no_match_for_each(
    tmp_path: Path,
) -> None:
    """A trailing `forEach` that matches nothing never calls `_handle_action`, so it must not
    silently swallow the final capture: it still lands on the last leaf step that actually ran
    before it (BE-XXXX)."""
    driver = FakeDriver([el("a", "A", ["button"])])  # no `item.*` elements to match
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"tap": {"id": "a"}},
                    {
                        "forEach": {
                            "sel": {"idMatches": "item.*"},
                            "as": "current",
                            "steps": [{"tap": {"id": "${vars.current}"}}],
                        }
                    },
                ],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
    )
    assert result.ok, result.failure
    tap_outcome = next(s for s in result.steps if s.action == "tap")
    names = {a.name for a in tap_outcome.artifacts}
    assert any(name.endswith("before.png") for name in names)
    assert any(name.endswith("after.png") for name in names)


class _FakeBridge:
    """Minimal `DomSource` for a `web` block: canned DOM elements, no-op writes."""

    def __init__(self, dom_elements: list[base.Element]) -> None:
        self._elements = dom_elements

    def query_dom(self, webview_id: str) -> list[base.Element]:
        return self._elements

    def tap_element(self, webview_id: str, point: base.Point) -> None:
        pass

    def type_text(self, webview_id: str, text: str) -> None:
        pass

    def scroll_to(self, webview_id: str, element_id: str) -> None:
        pass


def test_pre_step_capture_queries_the_web_driver_for_a_blocks_first_nested_step(
    tmp_path: Path,
) -> None:
    """The pre-step baseline for a `web` block's first nested step queries the *web* driver, not
    the native one, since `prev_after` is reset to `None` around the whole block (BE-0234 Unit 2)
    and the sink call always targets the native driver otherwise (BE-XXXX) — proven by content:
    the DOM-only element must appear in the written elements.json. The scenario's final capture
    (screenshot only, added since this is also the last step) never touches `elements` at all, so
    it needs no web-driver interaction of its own."""
    native_screen = [el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))]
    dom_elements: list[base.Element] = [
        el("confirm", "Confirm", ["button"], frame=(10.0, 10.0, 100.0, 20.0))
    ]
    bridge = _FakeBridge(dom_elements)
    driver = FakeDriver(native_screen)
    run_dir = tmp_path / "run1"
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "app.webview"},
                            "steps": [{"tap": {"id": "confirm"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(run_dir),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    leaf_outcome = next(s for s in result.steps if s.action == "tap")
    els_artifact = next(a for a in leaf_outcome.artifacts if a.kind == "elements")
    written = json.loads((run_dir / els_artifact.name).read_text(encoding="utf-8"))
    assert any(e["identifier"] == "confirm" for e in written)  # the DOM tree, not the native one
    # Exactly one `elements` entry: the final capture (this is also the last step) adds only a
    # second screenshot, never a second `elements`.
    assert sum(1 for a in leaf_outcome.artifacts if a.kind == "elements") == 1
    names = {a.name for a in leaf_outcome.artifacts}
    assert any(name.endswith("before.png") for name in names)
    assert any(name.endswith("after.png") for name in names)


def test_pre_step_and_final_captures_write_content_from_the_same_pre_action_moment(
    tmp_path: Path,
) -> None:
    """Content check, not just call ordering: every step's elements.json holds the pre-action tree
    it acted on — including the scenario's last step, whose final capture only adds a screenshot
    (`after.png`), never re-capturing `elements` (BE-XXXX). `elements.json` has one fixed filename,
    so if the final capture re-wrote it, the last step's `elements.json` would silently disagree
    with the `before.png` the editor's `screenshotUrl` still resolves to — a real review finding
    this test now guards against.
    """

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind != "tap":
            return
        if arg == {"id": "a"}:
            d.screen = [el("a", "A", ["button"]), el("b", "B", ["button"]), el("mid", "Mid")]
        elif arg == {"id": "b"}:
            d.screen = [el("a", "A", ["button"]), el("b", "B", ["button"]), el("final", "Final")]

    driver = FakeDriver([el("a", "A", ["button"]), el("b", "B", ["button"])], react=react)
    run_dir = tmp_path / "run1"
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}}]}),
        clock=FakeClock(),
        sink=FileSink(run_dir),
    )
    assert result.ok, result.failure

    def _tree_ids(step_index: int) -> set[str | None]:
        art = next(a for a in result.steps[step_index].artifacts if a.kind == "elements")
        data = json.loads((run_dir / art.name).read_text(encoding="utf-8"))
        return {e["identifier"] for e in data}

    # step0's elements.json is the pre-step baseline, written before its own tap ran — the
    # original screen, not the one its own tap produced.
    assert _tree_ids(0) == {"a", "b"}
    # step1 is the last step, but its elements.json is *also* the pre-step baseline — written
    # before its own tap ran, matching the moment `before.png` shows. "final" (produced only by
    # step1's own tap) never appears, since the final capture does not touch `elements`.
    assert _tree_ids(1) == {"a", "b", "mid"}
    assert "final" not in _tree_ids(1)
    # The final capture still adds the extra screenshot, visually showing the true end state.
    names = {a.name for a in result.steps[1].artifacts}
    assert any(name.endswith("after.png") for name in names)


def test_extract_still_reads_the_settled_post_action_value(tmp_path: Path) -> None:
    """`extract` is unaffected by the pre-step baseline: it still copies out the settled
    post-action value, never the pre-step snapshot (BE-XXXX leaves BE-0299 untouched)."""

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = [el("counter", "counter", value="1"), el("go", "Go", ["button"])]

    driver = FakeDriver(
        [el("counter", "counter", value="0"), el("go", "Go", ["button"])], react=react
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "go"},
                        "extract": {"n": {"sel": {"id": "counter"}, "prop": "value"}},
                    },
                    {"assert": [{"value": {"sel": {"id": "counter"}, "equals": "${vars.n}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
    )
    assert result.ok, result.failure

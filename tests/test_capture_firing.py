"""Tests for evidence firing in the run loop.

Every step captures two instant baselines: a pre-step one (screenshot.before + elements.before, taken
before the step acts, BE-0341) and a post-step one that always records screenshot.after + elements,
so both halves of the pair exist for every step whatever the scenario asked for.
`elements.json` has one fixed filename, so the post-step write replaces the pre-action tree: the
tree a step records describes the screen its action produced, the same moment `after.png` shows and
the moment a viewer draws element frames for.
capturePolicy / inline `capture` add extra instant kinds onto the post-step call — never
`screenshot.before`, which the pre-step baseline already wrote (filtered out as redundant). Interval
kinds (video / deviceLog / appTrace) are heavy and opt-in (BE-0028): recorded once for the whole
scenario, but only when the scenario actually requests that kind.

The post-step capture arrives as two calls, not one: the screenshot is taken first, then the tree is
read and written, so the image is never older than the tree a viewer draws element frames from.

Every scenario below has exactly one step, so each sees exactly three capture calls: the pre-step
baseline, the post-action shutter, and the tree read that follows it.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.evidence_rules import requested_intervals
from bajutsu.common.orchestrator.waits import WaitTrace
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import Artifact, FileSink, intervals
from bajutsu.scenario import Scenario

BASELINE_BEFORE = ["screenshot.before", "elements.before"]
BASELINE_AFTER = ["screenshot.after"]
BASELINE_TREE = ["elements"]


def _baseline_calls(*extra: str, step_id: str = "x/step0") -> list[tuple[str, list[str]]]:
    """The three capture calls a leaf step makes: the pre-step baseline, the post-step shutter, and
    the post-step tree read that follows it — carrying any extra kinds the scenario asked for."""
    return [
        (step_id, BASELINE_BEFORE),
        (step_id, BASELINE_AFTER),
        (step_id, [*BASELINE_TREE, *extra]),
    ]


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []  # instant capture calls
        self.scenario_intervals: list[tuple[str, list[str]]] = []

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
        elements_source: str | None = None,
    ) -> list[Artifact]:
        if kinds:
            self.calls.append((step_id, kinds))
        # Named the way a `FileSink` names them — `evidence.capture` returns the bare filename
        # (`after.png`) and `FileSink.capture` re-roots it under the step id — so a caller reading
        # the returned artifacts sees what a real run's manifest would carry.
        return [
            Artifact(f"{step_id}/{token.partition('.')[2] or 'after'}.png", "screenshot", "driver")
            for token in kinds
            if token.partition(".")[0] == "screenshot"
        ]

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        self.scenario_intervals.append((scenario_id, kinds))
        return []

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        return []

    def wait_diagnostic(
        self, step_id: str, *, trace: WaitTrace, elements: list[base.Element]
    ) -> Artifact | None:
        return None  # these tests assert on captures and intervals, never on a wait diagnostic


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }


def _scn(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


def test_baseline_always_fires() -> None:
    # No capturePolicy / inline capture at all: both instant baselines still fire — the pre-step
    # one every step gets, and the post-step one this (also last) step gets too.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(driver, _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}), sink=sink)
    assert sink.calls == _baseline_calls()


# --- requested_intervals: heavy intervals are opt-in (BE-0028 guard #2) --------------------


def test_requested_intervals_empty_by_default() -> None:
    scn = _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert requested_intervals(scn) == []


def test_requested_intervals_from_inline_capture() -> None:
    scn = _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["video"]}]})
    assert requested_intervals(scn) == ["video"]


def test_requested_intervals_from_capture_policy_in_canonical_order() -> None:
    scn = _scn(
        {
            "name": "x",
            "capturePolicy": [
                {"on": {"result": "error"}, "capture": ["appTrace", "deviceLog", "video"]}
            ],
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    # ordered video, deviceLog, appTrace regardless of request order
    assert requested_intervals(scn) == ["video", "deviceLog", "appTrace"]


def test_requested_intervals_recurses_into_nested_steps() -> None:
    scn = _scn(
        {
            "name": "x",
            "steps": [
                {
                    "forEach": {
                        "sel": {"idMatches": "row.*"},
                        "as": "r",
                        "steps": [{"tap": {"id": "${vars.r}"}, "capture": ["appTrace"]}],
                    }
                }
            ],
        }
    )
    assert requested_intervals(scn) == ["appTrace"]


def test_requested_intervals_ignores_instant_kinds() -> None:
    scn = _scn(
        {"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["screenshot", "elements"]}]}
    )
    assert requested_intervals(scn) == []


def test_action_trigger_adds_to_baseline() -> None:
    # The rule requests `actionLog` rather than a `screenshot` modifier: `.before` is always
    # redundant with the pre-step baseline (filtered post-step) and `.after` would be
    # indistinguishable from this (also last) step's own final-step baseline.
    driver = FakeDriver([_el("home.submit", "Submit")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [{"tap": {"id": "home.submit"}}],
                "capturePolicy": [
                    {
                        "on": {"action": "tap", "idMatches": "*.submit"},
                        "capture": ["actionLog"],
                    },
                ],
            }
        ),
        sink=sink,
    )
    assert sink.calls == _baseline_calls("actionLog")


def test_bare_screenshot_token_does_not_double_the_always_on_after_shot() -> None:
    # A bare `screenshot` means `screenshot.after` (the modifier defaults to `after`), so a scenario
    # spelling it that way must not shoot `after.png` twice against the always-on token — which
    # would also leave a duplicate artifact entry in the manifest for that one file.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["screenshot"]}]}),
        sink=sink,
    )
    assert sink.calls == _baseline_calls()


def test_an_elements_modifier_does_not_double_the_always_on_tree_read() -> None:
    # `capture()` ignores the modifier for `elements` — `elements.after`, `.before`, `.around` and
    # `.onError` all write the one `elements.json` — so a scenario spelling it any of those ways
    # must not fire beside the always-on `elements`, which would write the file twice and leave a
    # duplicate artifact entry in the manifest (review follow-up).
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["elements.after"]}]}),
        sink=sink,
    )
    assert sink.calls == _baseline_calls()


def test_action_trigger_skips_on_id_mismatch() -> None:
    driver = FakeDriver([_el("home.cancel", "Cancel")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [{"tap": {"id": "home.cancel"}}],
                "capturePolicy": [
                    {
                        "on": {"action": "tap", "idMatches": "*.submit"},
                        "capture": ["actionLog"],
                    },
                ],
            }
        ),
        sink=sink,
    )
    # Only the two baselines: the policy did not fire, so no middle call.
    assert sink.calls == _baseline_calls()


def test_screen_changed_trigger_adds_to_baseline() -> None:
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [{"tap": {"id": "go"}}],
                "capturePolicy": [{"on": {"event": "screenChanged"}, "capture": ["actionLog"]}],
            }
        ),
        sink=sink,
    )
    assert sink.calls == _baseline_calls("actionLog")


def test_error_trigger_is_the_safety_net() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [{"tap": {"id": "missing"}}],
                "capturePolicy": [{"on": {"result": "error"}, "capture": ["actionLog"]}],
            }
        ),
        sink=sink,
    )
    assert sink.calls == _baseline_calls("actionLog")


def test_inline_raw_tree_fires_post_step_beside_the_tree_it_describes() -> None:
    # `rawTree` stays on the post-step call rather than joining the pre-step baseline:
    # `write_raw_tree` persists the driver's *last* read, and the post-step baseline's always-on
    # `elements` token re-reads the tree on every step — so a pre-step dump would describe the
    # pre-action read while the elements.json beside it describes the post-action one.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["rawTree"]}]}),
        sink=sink,
    )
    assert sink.calls == _baseline_calls("rawTree")


def test_inline_raw_tree_and_elements_together_both_go_post_step() -> None:
    # Naming both kinds inline (`capture: [elements, rawTree]`) is the combination an author
    # reaching for this feature is most likely to write. The inline `elements` dedupes against the
    # always-on token, and both fire post-step, where `capture()`'s own stable sort pairs them on
    # the same (post-action) read.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["elements", "rawTree"]}]}),
        sink=sink,
    )
    assert sink.calls == _baseline_calls("rawTree")


def test_inline_raw_tree_is_dropped_inside_a_web_block() -> None:
    # Inside a `web` block, the post-step capture call always targets the native driver (a
    # `WebContextDriver` cannot screenshot) while `elements.json` there is written from the *web*
    # driver's tree — so a `rawTree` request would ask the native driver for a dump describing an
    # unrelated backend and read entirely. Dropped rather than serviced, both here (it is never
    # pre-captured either, for the same driver mismatch) and post-step.
    class _Bridge:
        def query_dom(self, webview_id: str) -> list[base.Element]:
            return [_el("confirm", "Confirm")]

        def tap_element(self, webview_id: str, point: tuple[float, float]) -> None:
            pass

        def type_text(self, webview_id: str, text: str) -> None:
            pass

        def scroll_to(self, webview_id: str, element_id: str) -> None:
            pass

    driver = FakeDriver([_el("app.webview", "WebView")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "app.webview"},
                            "steps": [{"tap": {"id": "confirm"}, "capture": ["rawTree"]}],
                        }
                    }
                ],
            }
        ),
        sink=sink,
        webview_bridge=_Bridge(),
    )
    assert all("rawTree" not in kinds for _, kinds in sink.calls)


def test_inline_interval_token_is_recorded_scenario_wide_not_per_step() -> None:
    # deviceLog is an interval kind: it is recorded for the whole scenario, so it
    # does not appear as a per-step instant capture (only the two baselines do).
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["deviceLog"]}]}),
        sink=sink,
    )
    assert sink.calls == _baseline_calls()
    assert sink.scenario_intervals == [("x", ["deviceLog"])]  # opt-in: only the requested kind


def test_multiple_inline_interval_tokens_are_recorded_scenario_wide() -> None:
    # Multiple interval kinds are scenario-wide recordings: they should not appear
    # as per-step instant captures, but all requested kinds should be started once.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [{"tap": {"id": "a"}, "capture": ["video", "deviceLog"]}],
            }
        ),
        sink=sink,
    )
    assert sink.calls == _baseline_calls()
    assert sink.scenario_intervals == [("x", ["video", "deviceLog"])]


class IntervalSink:
    """Records scenario-level interval recordings and returns artifacts for them."""

    def __init__(self) -> None:
        self.started: list[tuple[str, list[str]]] = []
        self.finished: list[str] = []

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
        elements_source: str | None = None,
    ) -> list[Artifact]:
        return []

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        self.started.append((scenario_id, kinds))
        return [
            intervals.Interval(kind=k.partition(".")[0], path=Path(f"{scenario_id}/{k}.bin"))
            for k in kinds
        ]

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        self.finished.append(scenario_id)
        return [Artifact(name=str(i.path), kind=i.kind, provider="simctl") for i in started]

    def wait_diagnostic(
        self, step_id: str, *, trace: WaitTrace, elements: list[base.Element]
    ) -> Artifact | None:
        return None  # these tests assert on captures and intervals, never on a wait diagnostic


def test_scenario_intervals_opt_in_only() -> None:
    # No capture asks for an interval -> none recorded (BE-0028: heavy intervals are opt-in).
    driver = FakeDriver([_el("a", "A")])
    sink = IntervalSink()
    result = run_scenario(
        driver, _scn({"name": "My Scn", "steps": [{"tap": {"id": "a"}}]}), sink=sink
    )
    assert sink.started == [("my-scn", [])]
    assert sink.finished == ["my-scn"]
    assert [a.kind for a in result.artifacts] == []


def test_scenario_records_only_the_requested_interval() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = IntervalSink()
    result = run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["video"]}]}),
        sink=sink,
    )
    assert sink.started == [("x", ["video"])]
    assert [a.kind for a in result.artifacts] == ["video"]


def test_requested_interval_recorded_even_when_a_step_fails() -> None:
    # An opted-in interval is still finalized on failure (the finally block).
    driver = FakeDriver([_el("a", "A")])
    sink = IntervalSink()
    result = run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "capturePolicy": [{"on": {"result": "error"}, "capture": ["video"]}],
                "steps": [{"tap": {"id": "missing"}}],
            }
        ),
        sink=sink,
    )
    assert not result.ok
    assert sink.finished == ["x"]
    assert [a.kind for a in result.artifacts] == ["video"]


def test_screen_changed_shares_query_with_evidence(tmp_path: Path) -> None:
    """With screenChanged capturePolicy, the post-step query() is shared between
    screen_changed detection and evidence capture (elements.json), not called twice."""

    class _State:
        def __init__(self) -> None:
            self.queries_after_tap = 0
            self.tapped = False

    state = _State()

    class CountingDriver(FakeDriver):
        def query(self) -> list[base.Element]:
            if state.tapped:
                state.queries_after_tap += 1
            return super().query()

        def tap(self, sel: base.Selector) -> None:
            super().tap(sel)
            state.tapped = True

    next_screen = [_el("done", "Done")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = next_screen

    driver = CountingDriver([_el("go", "Go")], react=react)
    sink = FileSink(tmp_path / "run1")
    result = run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [{"tap": {"id": "go"}}],
                "capturePolicy": [
                    {"on": {"event": "screenChanged"}, "capture": ["screenshot.before"]}
                ],
            }
        ),
        sink=sink,
    )
    assert result.ok
    # After tap: 1 shared query for screen_changed + evidence (not 2 separate ones)
    assert state.queries_after_tap == 1, (
        f"expected 1 post-step query (shared), got {state.queries_after_tap}"
    )
    # elements.json should still be written
    assert (tmp_path / "run1" / "x" / "step0" / "elements.json").exists()


# --- config-level `capture` (Effective.capture, BE's `defaults.capture`) -------------------


def test_config_capture_is_a_baseline_guarantee_on_every_step() -> None:
    # No capturePolicy / inline capture at all: the config-level `capture` still fires post-step,
    # unconditionally, on top of the two automatic baselines.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}),
        sink=sink,
        capture=["actionLog"],
    )
    assert sink.calls == _baseline_calls("actionLog")


def test_config_capture_dedupes_against_inline_and_policy() -> None:
    driver = FakeDriver([_el("home.submit", "Submit")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "capturePolicy": [
                    {"on": {"action": "tap", "idMatches": "*.submit"}, "capture": ["actionLog"]}
                ],
                "steps": [{"tap": {"id": "home.submit"}, "capture": ["actionLog"]}],
            }
        ),
        sink=sink,
        capture=["actionLog", "elements"],
    )
    # actionLog appears once (inline, policy, and config all name it); the config's `elements`
    # dedupes against the always-on token the post-step baseline already leads with.
    assert sink.calls == _baseline_calls("actionLog")


def test_config_capture_drops_screenshot_before_as_redundant() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}),
        sink=sink,
        capture=["screenshot.before"],
    )
    # Redundant with the pre-step baseline, so it contributes nothing extra post-step.
    assert sink.calls == _baseline_calls()


def test_config_capture_requests_an_interval_kind() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}),
        sink=sink,
        capture=["video"],
    )
    assert sink.calls == _baseline_calls()
    assert sink.scenario_intervals == [("x", ["video"])]


def test_requested_intervals_from_config_capture() -> None:
    scn = _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert requested_intervals(scn, ["deviceLog"]) == ["deviceLog"]


def test_no_config_capture_leaves_behavior_unchanged() -> None:
    # A caller that passes no `capture` (the default) sees the unchanged capturePolicy/inline-only
    # behavior — this is what every other test above already exercises implicitly.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(driver, _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}), sink=sink)
    assert sink.calls == _baseline_calls()


def test_file_sink_writes_baseline_elements(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}),
        sink=FileSink(tmp_path / "run1"),
    )
    # The baseline writes the element tree for the step even with no capturePolicy,
    # nested under the scenario's dir (slug of "x").
    assert (tmp_path / "run1" / "x" / "step0" / "elements.json").exists()

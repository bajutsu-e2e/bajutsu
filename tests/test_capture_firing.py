"""Tests for evidence firing in the run loop.

Every step always captures an instant baseline (screenshot.after + elements);
capturePolicy / inline `capture` add extra instant captures on top. Interval kinds
(video / deviceLog / appTrace) are heavy and opt-in (BE-0028): recorded once for the
whole scenario, but only when the scenario actually requests that kind.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu import intervals
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import Artifact, FileSink
from bajutsu.orchestrator import run_scenario
from bajutsu.orchestrator.evidence_rules import requested_intervals
from bajutsu.scenario import Scenario

BASELINE = ["screenshot.after", "elements"]


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
    ) -> list[Artifact]:
        if kinds:
            self.calls.append((step_id, kinds))
        return []

    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]:
        self.scenario_intervals.append((scenario_id, kinds))
        return []

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        return []


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _scn(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


def test_baseline_always_fires() -> None:
    # No capturePolicy / inline capture at all: the instant baseline still fires.
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(driver, _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}), sink=sink)
    assert sink.calls == [("x/step0", BASELINE)]


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
            "capturePolicy": [{"on": {"result": "error"}, "capture": ["deviceLog", "video"]}],
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    # ordered video, deviceLog, appTrace regardless of request order
    assert requested_intervals(scn) == ["video", "deviceLog"]


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
                        "capture": ["screenshot.before"],
                    },
                ],
            }
        ),
        sink=sink,
    )
    assert sink.calls == [("x/step0", [*BASELINE, "screenshot.before"])]


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
                        "capture": ["screenshot.before"],
                    },
                ],
            }
        ),
        sink=sink,
    )
    assert sink.calls == [("x/step0", BASELINE)]  # only the baseline, policy did not fire


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
                "capturePolicy": [
                    {"on": {"event": "screenChanged"}, "capture": ["screenshot.before"]}
                ],
            }
        ),
        sink=sink,
    )
    assert sink.calls == [("x/step0", [*BASELINE, "screenshot.before"])]


def test_error_trigger_is_the_safety_net() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn(
            {
                "name": "x",
                "steps": [{"tap": {"id": "missing"}}],
                "capturePolicy": [{"on": {"result": "error"}, "capture": ["screenshot.before"]}],
            }
        ),
        sink=sink,
    )
    assert sink.calls == [("x/step0", [*BASELINE, "screenshot.before"])]


def test_inline_interval_token_is_recorded_scenario_wide_not_per_step() -> None:
    # deviceLog is an interval kind: it is recorded for the whole scenario, so it
    # does not appear as a per-step instant capture (only the baseline does).
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["deviceLog"]}]}),
        sink=sink,
    )
    assert sink.calls == [("x/step0", BASELINE)]
    assert sink.scenario_intervals == [("x", ["deviceLog"])]  # opt-in: only the requested kind


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
    queries_after_tap: list[int] = [0]
    tapped = [False]

    class CountingDriver(FakeDriver):
        def query(self) -> list[base.Element]:
            if tapped[0]:
                queries_after_tap[0] += 1
            return super().query()

        def tap(self, sel: base.Selector) -> None:
            super().tap(sel)
            tapped[0] = True

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
    assert queries_after_tap[0] == 1, (
        f"expected 1 post-step query (shared), got {queries_after_tap[0]}"
    )
    # elements.json should still be written
    assert (tmp_path / "run1" / "x" / "step0" / "elements.json").exists()


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

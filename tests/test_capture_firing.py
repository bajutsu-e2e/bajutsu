"""Tests for capturePolicy firing in the run loop."""

from __future__ import annotations

from pathlib import Path

from simyoke.drivers import base
from simyoke.drivers.fake import FakeDriver
from simyoke.evidence import FileSink
from simyoke.orchestrator import run_scenario
from simyoke.scenario import Scenario


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def capture(self, driver: base.Driver, step_id: str, kinds: list[str]) -> None:
        self.calls.append((step_id, kinds))


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


def test_action_trigger_fires_on_id_match() -> None:
    driver = FakeDriver([_el("home.submit", "Submit")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({
            "name": "x",
            "steps": [{"tap": {"id": "home.submit"}}],
            "capturePolicy": [
                {"on": {"action": "tap", "idMatches": "*.submit"},
                 "capture": ["screenshot.after", "elements"]},
            ],
        }),
        sink=sink,
    )
    assert sink.calls == [("step0", ["screenshot.after", "elements"])]


def test_action_trigger_skips_on_id_mismatch() -> None:
    driver = FakeDriver([_el("home.cancel", "Cancel")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({
            "name": "x",
            "steps": [{"tap": {"id": "home.cancel"}}],
            "capturePolicy": [
                {"on": {"action": "tap", "idMatches": "*.submit"}, "capture": ["elements"]},
            ],
        }),
        sink=sink,
    )
    assert sink.calls == []


def test_screen_changed_trigger() -> None:
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({
            "name": "x",
            "steps": [{"tap": {"id": "go"}}],
            "capturePolicy": [{"on": {"event": "screenChanged"}, "capture": ["elements"]}],
        }),
        sink=sink,
    )
    assert sink.calls == [("step0", ["elements"])]


def test_error_trigger_is_the_safety_net() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({
            "name": "x",
            "steps": [{"tap": {"id": "missing"}}],
            "capturePolicy": [{"on": {"result": "error"}, "capture": ["screenshot", "elements"]}],
        }),
        sink=sink,
    )
    assert sink.calls == [("step0", ["screenshot", "elements"])]


def test_inline_capture_fires() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(
        driver,
        _scn({"name": "x", "steps": [{"tap": {"id": "a"}, "capture": ["deviceLog"]}]}),
        sink=sink,
    )
    assert sink.calls == [("step0", ["deviceLog"])]


def test_no_capture_no_fire() -> None:
    driver = FakeDriver([_el("a", "A")])
    sink = RecordingSink()
    run_scenario(driver, _scn({"name": "x", "steps": [{"tap": {"id": "a"}}]}), sink=sink)
    assert sink.calls == []


def test_file_sink_writes_elements(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])
    run_scenario(
        driver,
        _scn({
            "name": "x",
            "steps": [{"tap": {"id": "a"}}],
            "capturePolicy": [{"on": {"action": "tap"}, "capture": ["elements"]}],
        }),
        sink=FileSink(tmp_path / "run1"),
    )
    assert (tmp_path / "run1" / "step0" / "elements.json").exists()

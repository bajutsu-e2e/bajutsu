"""Tests for lightweight evidence capture."""

from __future__ import annotations

import json
from pathlib import Path

from simpilot.drivers import base
from simpilot.drivers.fake import FakeDriver
from simpilot.evidence import capture, write_elements


def _el(identifier: str, label: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_write_elements(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A"), _el("b", "B")])
    path = write_elements(driver, tmp_path / "step0")
    assert path.name == "elements.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [e["identifier"] for e in data] == ["a", "b"]


def test_capture_elements_and_screenshot(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])
    written = capture(driver, tmp_path / "step0", ["elements", "screenshot.after"])
    assert written == ["elements.json", "after.png"]
    assert (tmp_path / "step0" / "elements.json").exists()
    # FakeDriver records the screenshot call with the path it was given.
    assert ("screenshot", str(tmp_path / "step0" / "after.png")) in driver.actions

"""Tests for lightweight evidence capture."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import capture, write_elements


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


def test_write_elements_uses_provided_elements(tmp_path: Path) -> None:
    """When pre-queried elements are provided, write_elements uses them
    instead of calling driver.query()."""
    driver = FakeDriver([_el("from_driver", "D")])
    provided = [_el("provided", "P")]
    path = write_elements(driver, tmp_path / "step0", elements=provided)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["identifier"] == "provided"


def test_capture_uses_provided_elements(tmp_path: Path) -> None:
    """capture() passes provided elements through to write_elements."""
    driver = FakeDriver([_el("from_driver", "D")])
    provided = [_el("provided", "P")]
    capture(driver, tmp_path / "step0", ["elements"], elements=provided)
    data = json.loads((tmp_path / "step0" / "elements.json").read_text(encoding="utf-8"))
    assert data[0]["identifier"] == "provided"


def test_capture_elements_and_screenshot(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])
    written = capture(driver, tmp_path / "step0", ["elements", "screenshot.after"])
    assert [(a.name, a.kind, a.provider) for a in written] == [
        ("elements.json", "elements", "driver"),
        ("after.png", "screenshot", "driver"),
    ]
    assert (tmp_path / "step0" / "elements.json").exists()
    # FakeDriver records the screenshot call with the path it was given.
    assert ("screenshot", str(tmp_path / "step0" / "after.png")) in driver.actions


def test_capture_no_writing_kinds_leaves_dir_uncreated(tmp_path: Path) -> None:
    """capture() creates the step dir only when it actually writes a file; a kind it
    does not handle here (e.g. an interval kind) must leave the dir untouched, as before."""
    driver = FakeDriver([_el("a", "A")])
    step_dir = tmp_path / "step0"
    assert capture(driver, step_dir, ["video"]) == []
    assert not step_dir.exists()


def test_capture_creates_dir_once_for_writing_kinds(tmp_path: Path) -> None:
    """A capture with both writing kinds lands both files under a freshly created step dir."""
    driver = FakeDriver([_el("a", "A")])
    step_dir = tmp_path / "step0"
    capture(driver, step_dir, ["elements", "screenshot.after"])
    assert (step_dir / "elements.json").exists()
    assert ("screenshot", str(step_dir / "after.png")) in driver.actions

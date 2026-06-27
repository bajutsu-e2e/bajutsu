"""Tests for lightweight evidence capture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_capture_creates_dir_once_for_writing_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture of two writing kinds creates the step dir exactly once, not once per writer.

    Counts `Path.mkdir` calls on the step dir: a regression to per-writer `mkdir()` would make it
    fire two-plus times. Both files still land under the freshly created dir.
    """
    mkdirs: list[Path] = []
    real_mkdir = Path.mkdir

    def counting_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        mkdirs.append(self)
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)

    driver = FakeDriver([_el("a", "A")])
    step_dir = tmp_path / "step0"
    capture(driver, step_dir, ["elements", "screenshot.after"])
    assert (step_dir / "elements.json").exists()
    assert ("screenshot", str(step_dir / "after.png")) in driver.actions
    assert mkdirs.count(step_dir) == 1  # the step dir is created once, not per writing kind


def test_filesink_dispatches_intervals_to_web_provider(tmp_path: Path) -> None:
    # When a web interval provider is injected, the sink uses it (Playwright-native) instead of
    # the simctl starters, even though the web lane carries a (synthetic) udid.
    from bajutsu import intervals
    from bajutsu.evidence import FileSink

    calls: list[tuple[str, str]] = []

    def web_interval(kind: str, path: Path) -> intervals.Interval | None:
        calls.append((kind, path.name))
        if kind == "deviceLog":
            return intervals.Interval(kind="deviceLog", path=path, provider="playwright")
        return None  # video etc. not provided in this slice

    sink = FileSink(tmp_path, udid="web-0", web_interval=web_interval)
    started = sink.start_scenario_intervals("00-s", ["deviceLog", "video"])

    assert calls == [("deviceLog", "device.log"), ("video", "scenario.mp4")]
    # Only the provided (deviceLog) interval is started; the unsupported video is skipped.
    assert [iv.kind for iv in started] == ["deviceLog"]
    assert started[0].provider == "playwright"


def test_filesink_without_web_provider_uses_udid_gate(tmp_path: Path) -> None:
    from bajutsu.evidence import FileSink

    # No udid and no web provider: intervals are skipped (the fake/headless path).
    sink = FileSink(tmp_path, udid=None)
    assert sink.start_scenario_intervals("00-s", ["deviceLog"]) == []

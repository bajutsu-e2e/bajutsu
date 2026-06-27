"""Tests for lightweight evidence capture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import FileSink, capture, write_elements


class _StubInterval:
    """A finished recording, standing in for the subprocess-backed `Interval` (an external
    boundary): `finish_scenario_intervals` only needs `stop()` / `kind` / `provider`."""

    def __init__(self, path: Path, kind: str = "deviceLog", provider: str = "idb") -> None:
        self._path = path
        self.kind = kind
        self.provider = provider

    def stop(self) -> Path:
        return self._path


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


def test_finish_scenario_intervals_redacts_then_emits_a_readable_file(tmp_path: Path) -> None:
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    f = tmp_path / "deviceLog.txt"
    f.write_text("auth token=topsecret here", encoding="utf-8")
    out = sink.finish_scenario_intervals("s", [_StubInterval(f)])
    assert [a.name for a in out] == ["deviceLog.txt"]
    assert "topsecret" not in f.read_text(encoding="utf-8")


def test_finish_scenario_intervals_drops_an_artifact_it_cannot_redact(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Redaction is a security control: if the evidence file can't be read to scrub it, the artifact
    # must not ship (fail closed) rather than reach the report unredacted. A directory at the file
    # path makes read_text raise IsADirectoryError (a real OSError) without mocking the filesystem.
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    unreadable = tmp_path / "deviceLog.txt"
    unreadable.mkdir()
    with caplog.at_level("WARNING"):
        out = sink.finish_scenario_intervals("s", [_StubInterval(unreadable)])
    assert out == []
    assert any("redact" in r.message.lower() for r in caplog.records)


def test_finish_scenario_intervals_emits_when_no_redactor_is_active(tmp_path: Path) -> None:
    # With no secrets configured the redactor is inactive, so an unreadable file is not a leak risk
    # and is still emitted (the fail-closed guard is scoped to the active-redaction case).
    sink = FileSink(tmp_path, udid="u")
    f = tmp_path / "deviceLog.txt"
    f.write_text("nothing secret", encoding="utf-8")
    out = sink.finish_scenario_intervals("s", [_StubInterval(f)])
    assert [a.name for a in out] == ["deviceLog.txt"]

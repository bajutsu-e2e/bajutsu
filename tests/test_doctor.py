"""Tests for the convention score (doctor)."""

from __future__ import annotations

from bajutsu.doctor import render, score
from bajutsu.drivers import base


def _el(identifier: str | None, traits: list[str], label: str = "x") -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits,
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_ready() -> None:
    screen = [
        _el("settings.open", ["button"]),
        _el("settings.reindex", ["button"]),
        _el("search.field", ["searchField"]),
        _el(None, ["staticText"]),  # not actionable -> ignored
    ]
    s = score(screen, ["settings", "search"])
    assert s.grade == "Ready"
    assert s.id_coverage == 1.0
    assert s.actionable == 3


def test_blocked_low_coverage() -> None:
    screen = [_el("settings.open", ["button"]), _el(None, ["button"], "無名")]
    s = score(screen, ["settings"])
    assert s.grade == "Blocked"
    assert s.id_coverage == 0.5
    assert len(s.missing_id) == 1


def test_partial_coverage() -> None:
    screen = [_el(f"settings.b{i}", ["button"]) for i in range(4)] + [_el(None, ["button"])]
    s = score(screen, ["settings"])
    assert s.id_coverage == 0.8
    assert s.grade == "Partial"


def test_blocked_duplicate() -> None:
    screen = [_el("settings.open", ["button"]), _el("settings.open", ["button"])]
    s = score(screen, ["settings"])
    assert s.grade == "Blocked"
    assert s.duplicates == ["settings.open"]


def test_partial_off_namespace() -> None:
    screen = [_el("settings.open", ["button"]), _el("foo.bar", ["button"])]
    s = score(screen, ["settings"])
    assert s.namespace_conformance == 0.5
    assert s.off_namespace == ["foo.bar"]
    assert s.grade == "Partial"


def test_no_actionable_is_blocked() -> None:
    # A screen with nothing actionable can't be "Ready": it's most likely blank, not yet loaded,
    # or the wrong screen — a false-positive doctor must surface, not paper over (BE-0024).
    s = score([_el(None, ["staticText"])], ["settings"])
    assert s.grade == "Blocked"
    assert s.no_actionable is True


def test_empty_screen_is_blocked() -> None:
    s = score([], ["settings"])
    assert s.grade == "Blocked"
    assert s.no_actionable is True


def test_no_actionable_render_points_at_the_likely_cause() -> None:
    s = score([], ["settings"])
    assert "no actionable elements" in render(s)


def test_a_screen_with_actionable_elements_is_not_flagged_no_actionable() -> None:
    s = score([_el("settings.open", ["button"])], ["settings"])
    assert s.no_actionable is False


def test_render_mentions_grade() -> None:
    s = score([_el("settings.open", ["button"])], ["settings"])
    assert "grade: Ready" in render(s)

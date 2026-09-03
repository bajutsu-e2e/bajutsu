"""Tests for the report's rich-text decomposition (bajutsu/common/report/richtext.py).

Every selector, matcher, assertion and step in an HTML report is rendered from the `(class, text)`
parts this module produces: the text is what a reader sees, and the class is what colors it. The
pieces are pure — dicts in, parts out — so each branch is covered directly here rather than through
a rendered report. The `?` fallbacks matter as much as the happy paths: a step shape the report does
not recognize must degrade to a visible placeholder instead of raising while writing evidence.

The last section covers the two formatting primitives beneath it (`bajutsu/common/report/format.py`),
whose error paths keep a missing or corrupt artifact file from taking the whole report down.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.report.format import Part, _read_json, _read_lines, _status_class
from bajutsu.common.report.richtext import (
    _assert_parts,
    _countmatch_parts,
    _join,
    _pt_parts,
    _request_parts,
    _sel_parts,
    _step_desc_parts,
    _textmatch_parts,
    _wait_parts,
)


def _text(parts: list[Part]) -> str:
    """The rendered text of a part list, with the token classes dropped."""
    return "".join(text for _cls, text in parts)


def _classes(parts: list[Part]) -> list[str]:
    """The non-empty token classes, in order — what the template colors."""
    return [cls for cls, _text in parts if cls]


# --- joining part groups ---------------------------------------------------------------------------


def test_joining_separates_groups_with_a_single_space() -> None:
    assert _join([("id", "#a")], [("str", "“b”")]) == [("id", "#a"), ("", " "), ("str", "“b”")]


def test_joining_skips_empty_groups_rather_than_emitting_stray_separators() -> None:
    # The one rule the callers rely on: they hand over a group per optional field, and an absent
    # field must leave no trace — neither a leading space nor a doubled one.
    assert _join([], [("id", "#a")], [], [("num", "n=1")], []) == [
        ("id", "#a"),
        ("", " "),
        ("num", "n=1"),
    ]
    assert _join([], []) == []


# --- selectors -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sel", "expected"),
    [
        ({"id": "home.title"}, "#home.title"),
        ({"idMatches": "home\\..*"}, "id~home\\..*"),
        ({"label": "Sign in"}, "“Sign in”"),
        ({"labelMatches": "Sign.*"}, "label~/Sign.*/"),
        ({"traits": ["button", "selected"]}, "[button, selected]"),
        ({"value": "42"}, "value=“42”"),
        ({"index": 2}, "n=2"),
    ],
)
def test_each_selector_field_renders(sel: dict[str, Any], expected: str) -> None:
    assert _text(_sel_parts(sel)) == expected


def test_an_or_list_of_ids_joins_the_candidates() -> None:
    # BE-0221: a list of id candidates is an OR, shown as such rather than as a raw Python list.
    assert _text(_sel_parts({"id": ["home.title", "home.header"]})) == "#home.title | home.header"


def test_selector_fields_combine_in_a_stable_order() -> None:
    parts = _sel_parts({"id": "row", "label": "Inbox", "index": 1})
    assert _text(parts) == "#row “Inbox” n=1"
    assert _classes(parts) == ["id", "str", "num"]


def test_a_within_selector_nests() -> None:
    assert _text(_sel_parts({"label": "Save", "within": {"id": "dialog"}})) == (
        "“Save” within(#dialog)"
    )


def test_an_empty_selector_degrades_to_a_placeholder() -> None:
    assert _text(_sel_parts({})) == "?"


# --- points and matchers ---------------------------------------------------------------------------


def test_a_point_renders_as_a_pair() -> None:
    parts = _pt_parts([10, 20.5])
    assert _text(parts) == "(10, 20.5)"
    assert _classes(parts) == ["num", "num"]


@pytest.mark.parametrize("value", [None, "nope", [1], (1, 2, 3)])
def test_a_malformed_point_degrades_to_a_placeholder(value: Any) -> None:
    assert _text(_pt_parts(value)) == "?"


@pytest.mark.parametrize(
    ("matcher", "expected"),
    [
        ({"equals": "hello"}, "== “hello”"),
        ({"contains": "ell"}, "contains “ell”"),
        ({"matches": "h.*o"}, "matches “h.*o”"),
        ({}, "?"),
    ],
)
def test_text_matchers_render(matcher: dict[str, Any], expected: str) -> None:
    assert _text(_textmatch_parts(matcher)) == expected


@pytest.mark.parametrize(
    ("matcher", "expected"),
    [
        ({"equals": 3}, "== 3"),
        ({"atLeast": 1}, "≥ 1"),
        ({"atMost": 5}, "≤ 5"),
        ({}, "?"),
    ],
)
def test_count_matchers_render(matcher: dict[str, Any], expected: str) -> None:
    assert _text(_countmatch_parts(matcher)) == expected


# --- request matchers ------------------------------------------------------------------------------


def test_a_request_matcher_splits_into_target_and_comparison() -> None:
    target, comparison = _request_parts(
        {"method": "post", "path": "/login", "status": 200, "count": 2}
    )
    # The method is upper-cased whatever the scenario wrote.
    assert _text(target) == "POST /login"
    assert _text(comparison) == "status == 200 count == 2"


def test_a_request_matcher_renders_its_pattern_forms() -> None:
    target, comparison = _request_parts(
        {"urlMatches": "https://.*", "pathMatches": "/v\\d+/", "bodyMatches": "ok"}
    )
    assert _text(target) == "url~/https://.*/ path~//v\\d+//"
    assert _text(comparison) == "body~/ok/"


def test_a_request_matcher_with_a_plain_url_shows_it() -> None:
    target, comparison = _request_parts({"url": "https://api.test/login"})
    assert _text(target) == "https://api.test/login"
    assert comparison == []


def test_an_empty_request_matcher_degrades_to_a_placeholder() -> None:
    target, comparison = _request_parts({})
    assert _text(target) == "?"
    assert comparison == []


# --- assertions ------------------------------------------------------------------------------------


def test_an_exists_assertion_carries_its_negation() -> None:
    kind, target, comparison = _assert_parts({"exists": {"id": "home.title"}})
    assert (kind, _text(target), comparison) == ("exists", "#home.title", [])
    kind, _target, _comparison = _assert_parts(
        {"exists": {"sel": {"id": "spinner"}, "negate": True}}
    )
    assert kind == "not exists"


@pytest.mark.parametrize("kind", ["value", "label"])
def test_a_text_assertion_pairs_its_selector_with_its_matcher(kind: str) -> None:
    got_kind, target, comparison = _assert_parts(
        {kind: {"sel": {"id": "field"}, "equals": "hello"}}
    )
    assert (got_kind, _text(target), _text(comparison)) == (kind, "#field", "== “hello”")


def test_a_count_assertion_pairs_its_selector_with_its_matcher() -> None:
    kind, target, comparison = _assert_parts({"count": {"sel": {"traits": ["cell"]}, "atLeast": 3}})
    assert (kind, _text(target), _text(comparison)) == ("count", "[cell]", "≥ 3")


@pytest.mark.parametrize("kind", ["enabled", "disabled", "selected"])
def test_a_state_assertion_carries_only_its_selector(kind: str) -> None:
    got_kind, target, comparison = _assert_parts({kind: {"id": "submit"}})
    assert (got_kind, _text(target), comparison) == (kind, "#submit", [])


def test_a_request_assertion_reuses_the_request_matcher() -> None:
    kind, target, comparison = _assert_parts({"request": {"method": "get", "status": 204}})
    assert (kind, _text(target), _text(comparison)) == ("request", "GET", "status == 204")


def test_a_visual_assertion_shows_its_baseline_and_threshold() -> None:
    kind, target, comparison = _assert_parts({"visual": {"baseline": "home.png", "threshold": 1.5}})
    assert (kind, _text(target), _text(comparison)) == ("visual", "home.png", "≤ 1.5%")


def test_a_visual_assertion_flags_its_scoping_and_exclusions() -> None:
    # BE-0171: an element-scoped comparison and any exclusions change what the number means, so
    # both are disclosed beside it.
    _kind, _target, comparison = _assert_parts(
        {
            "visual": {
                "baseline": "home.png",
                "element": {"id": "card"},
                "exclude": [{"id": "clock"}],
            }
        }
    )
    assert _text(comparison) == "≤ 0% · element-scoped · 1 excluded"


def test_an_unrecognized_assertion_degrades_to_a_placeholder() -> None:
    assert _assert_parts({"somethingNew": {}}) == ("?", [], [])


# --- steps -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["tap", "doubleTap"])
def test_a_tap_renders_its_selector(action: str) -> None:
    assert _text(_step_desc_parts(action, {"id": "ok"})) == "#ok"


def test_a_long_press_shows_its_duration() -> None:
    assert _text(_step_desc_parts("longPress", {"sel": {"id": "row"}, "duration": 1.5})) == (
        "#row · 1.5s"
    )


def test_typing_shows_the_text_its_field_and_whether_it_submits() -> None:
    assert _text(_step_desc_parts("type", {"text": "hello"})) == "“hello”"
    payload = {"text": "hello", "into": {"id": "search"}, "submit": True}
    assert _text(_step_desc_parts("type", payload)) == "“hello” into #search + submit"


def test_a_point_to_point_swipe_shows_both_ends() -> None:
    payload = {"from": [0, 100], "to": [0, 10]}
    assert _text(_step_desc_parts("swipe", payload)) == "(0, 100) → (0, 10)"


@pytest.mark.parametrize("action", ["swipe", "drag"])
def test_a_directional_gesture_shows_its_target_and_amount(action: str) -> None:
    # `swipe`'s {on,direction} form and `drag` share one payload shape, so they render identically.
    payload = {"direction": "up", "on": {"id": "list"}, "amount": 0.5}
    assert _text(_step_desc_parts(action, payload)) == "up on #list · 0.5"


def test_a_directional_gesture_without_an_amount_omits_it() -> None:
    assert _text(_step_desc_parts("drag", {"direction": "left", "on": {"id": "card"}})) == (
        "left on #card"
    )


def test_a_pinch_shows_its_scale() -> None:
    assert _text(_step_desc_parts("pinch", {"sel": {"id": "map"}, "scale": 2})) == "#map · ×2"


def test_a_rotate_shows_its_angle() -> None:
    assert _text(_step_desc_parts("rotate", {"sel": {"id": "map"}, "radians": 1.57})) == (
        "#map · 1.57 rad"
    )


def test_a_scroll_shows_its_direction_target_and_container() -> None:
    payload = {"direction": "down", "to": {"id": "footer"}}
    assert _text(_step_desc_parts("scroll", payload)) == "down to #footer"
    payload["within"] = {"id": "list"}
    assert _text(_step_desc_parts("scroll", payload)) == "down to #footer within #list"


def test_a_relaunch_names_itself() -> None:
    assert _text(_step_desc_parts("relaunch", {})) == "relaunch"


def test_an_unrecognized_action_renders_nothing() -> None:
    assert _step_desc_parts("teleport", {"id": "x"}) == []


# --- waits -----------------------------------------------------------------------------------------


def test_a_wait_for_a_selector_shows_its_budget() -> None:
    assert _text(_wait_parts({"for": {"id": "spinner"}, "timeout": 5})) == "for #spinner (≤5s)"


def test_a_wait_until_gone_shows_the_selector_it_waits_out() -> None:
    assert _text(_wait_parts({"until": {"gone": {"id": "spinner"}}, "timeout": 10})) == (
        "until gone #spinner (≤10s)"
    )


def test_a_wait_until_a_request_shows_target_and_comparison() -> None:
    payload = {"until": {"request": {"method": "get", "path": "/me", "status": 200}}, "timeout": 3}
    assert _text(_wait_parts(payload)) == "until request GET /me · status == 200 (≤3s)"


def test_a_wait_until_a_request_omits_an_empty_half() -> None:
    # Only the halves that say something are joined, so a matcher with no comparison reads cleanly.
    payload = {"until": {"request": {"path": "/me"}}, "timeout": 3}
    assert _text(_wait_parts(payload)) == "until request /me (≤3s)"


def test_an_unrecognized_wait_condition_is_shown_verbatim() -> None:
    assert _text(_wait_parts({"until": "idle", "timeout": 2})) == "until idle (≤2s)"


# --- formatting primitives (bajutsu/common/report/format.py) ---------------------------------------


def test_reading_lines_from_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    # A run whose log never materialized still gets a report; the section is simply absent.
    assert _read_lines(tmp_path, "absent.log", 10) == (None, 0)


def test_reading_lines_truncates_to_the_tail(tmp_path: Path) -> None:
    (tmp_path / "run.log").write_text("\n".join(str(i) for i in range(10)), encoding="utf-8")
    lines, total = _read_lines(tmp_path, "run.log", 3)
    assert (lines, total) == (["7", "8", "9"], 10)


def test_reading_json_from_a_missing_file_yields_none(tmp_path: Path) -> None:
    assert _read_json(tmp_path, "absent.json") is None


def test_reading_corrupt_json_yields_none(tmp_path: Path) -> None:
    # A truncated artifact (an interrupted run) must not take the report down with it.
    (tmp_path / "broken.json").write_text('{"a": ', encoding="utf-8")
    assert _read_json(tmp_path, "broken.json") is None


def test_reading_json_returns_the_parsed_document(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert _read_json(tmp_path, "ok.json") == {"a": 1}


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, "ok"), (301, "ok"), (399, "ok"), (404, "ng"), (500, "ng"), (None, ""), ("200", "")],
)
def test_a_status_is_classed_by_its_range(status: Any, expected: str) -> None:
    assert _status_class(status) == expected


def test_a_boolean_is_not_a_status() -> None:
    # `True` is an int in Python; a status column must not color it as a 2xx.
    assert _status_class(True) == ""

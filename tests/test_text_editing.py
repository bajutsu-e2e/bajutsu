"""Text-editing steps: select / clear / delete / copy (BE-0265).

Verified through the orchestrator's dispatch (FakeDriver records the driver calls) and the
selection-state contract `copy` relies on — `copy` fails deterministically without a prior
`select`, and every other action invalidates a standing selection.
"""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import load_scenarios


def _field(identifier: str, value: str | None) -> base.Element:
    return {
        "identifier": identifier,
        "label": None,
        "traits": [],
        "value": value,
        "frame": (0.0, 0.0, 100.0, 40.0),
    }


def _run(
    spec: str, screen: list[base.Element]
) -> tuple[bool, list[tuple[str, object]], str | None]:
    driver = FakeDriver(screen=screen)
    result = run_scenario(driver, load_scenarios(f"- name: s\n  steps:\n{spec}")[0])
    return result.ok, driver.actions, result.failure


def test_clear_focuses_then_backspaces_the_current_length() -> None:
    ok, actions, failure = _run(
        "    - clear: { into: { id: form.note } }\n", [_field("form.note", "hello")]
    )
    assert ok, failure
    # Focus the field, then remove exactly its current length (5) — agnostic to what it held.
    assert [a[0] for a in actions] == ["tap", "delete_text"]
    assert actions[1] == ("delete_text", 5)


def test_clear_on_empty_field_deletes_nothing() -> None:
    ok, actions, failure = _run(
        "    - clear: { into: { id: form.note } }\n", [_field("form.note", "")]
    )
    assert ok, failure
    assert [a[0] for a in actions] == ["tap"]  # nothing to delete


def test_delete_removes_count_from_end() -> None:
    ok, actions, failure = _run(
        "    - delete: { into: { id: form.note }, count: 3 }\n", [_field("form.note", "abcdef")]
    )
    assert ok, failure
    assert [a[0] for a in actions] == ["tap", "delete_text"]
    assert actions[1] == ("delete_text", 3)


def test_select_then_copy_succeeds() -> None:
    ok, actions, failure = _run(
        "    - select: { into: { id: form.note } }\n    - copy: {}\n",
        [_field("form.note", "hello")],
    )
    assert ok, failure
    assert [a[0] for a in actions] == ["tap", "select_all", "copy_selection"]


def test_copy_without_selection_fails() -> None:
    ok, actions, failure = _run("    - copy: {}\n", [_field("form.note", "hello")])
    assert not ok
    assert failure is not None and "selection" in failure
    assert not any(a[0] == "copy_selection" for a in actions)  # never actuated


def test_intervening_action_invalidates_the_selection() -> None:
    ok, _actions, failure = _run(
        "    - select: { into: { id: form.note } }\n    - tap: { id: other }\n    - copy: {}\n",
        [_field("form.note", "hello"), _field("other", "x")],
    )
    assert not ok
    assert failure is not None and "selection" in failure


def test_wait_between_select_and_copy_preserves_the_selection() -> None:
    # `wait` is a condition handled in the run loop, not an action routed through the dispatcher, so
    # it does not invalidate a standing selection: select → wait → copy is a valid sequence (BE-0265).
    ok, actions, failure = _run(
        "    - select: { into: { id: form.note } }\n"
        "    - wait: { for: { id: form.note }, timeout: 1 }\n"
        "    - copy: {}\n",
        [_field("form.note", "hello")],
    )
    assert ok, failure
    assert [a[0] for a in actions] == ["tap", "select_all", "copy_selection"]


def test_clear_with_no_reported_value_deletes_nothing() -> None:
    # A backend may report `value` as None (not ""); clear must treat that as an empty field and
    # backspace nothing, rather than fail on the missing length (BE-0265).
    ok, actions, failure = _run(
        "    - clear: { into: { id: form.note } }\n", [_field("form.note", None)]
    )
    assert ok, failure
    assert [a[0] for a in actions] == ["tap"]


def test_one_selection_can_be_copied_twice() -> None:
    ok, actions, failure = _run(
        "    - select: { into: { id: form.note } }\n    - copy: {}\n    - copy: {}\n",
        [_field("form.note", "hello")],
    )
    assert ok, failure
    assert [a[0] for a in actions].count("copy_selection") == 2

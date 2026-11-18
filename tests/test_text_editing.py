"""Text-editing steps: select / clear / delete / copy (BE-0265) and paste.

Verified through the orchestrator's dispatch (FakeDriver records the driver calls) and the
selection-state contract `copy` relies on — `copy` fails deterministically without a prior
`select`, and every other action invalidates a standing selection.
"""

from __future__ import annotations

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver, React
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.scenario import load_scenarios


class _NoTextSelectionDriver(FakeDriver):
    """A coordinate-only fake: no select-all handle, like the live/Appium iOS route (BE-0280)."""

    CAPABILITIES = frozenset(FakeDriver.CAPABILITIES - {base.Capability.TEXT_SELECTION})


def _field(identifier: str, value: str | None) -> base.Element:
    return {
        "identifier": identifier,
        "label": None,
        "traits": [],
        "value": value,
        "frame": (0.0, 0.0, 100.0, 40.0),
        "nativeZ": None,
    }


def _run(
    spec: str, screen: list[base.Element], react: React | None = None
) -> tuple[bool, list[tuple[str, object]], str | None]:
    driver = FakeDriver(screen=screen, react=react)
    result = run_scenario(driver, load_scenarios(f"- name: s\n  steps:\n{spec}")[0])
    return result.ok, driver.actions, result.failure


def test_clear_focuses_then_selects_all_and_backspaces_once() -> None:
    # Model a backend where select-all-then-backspace genuinely empties the field.
    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "delete_text":
            d.screen = [_field("form.note", "")]

    ok, actions, failure = _run(
        "    - clear: { into: { id: form.note } }\n", [_field("form.note", "hello")], react=react
    )
    assert ok, failure
    # Focus the field, select its whole content, then a single backspace removes the selection —
    # correct regardless of where the tap actually left the caret (FakeDriver advertises
    # TEXT_SELECTION). The field reads back empty, so no counted-backspace backstop follows.
    assert [a[0] for a in actions] == ["tap", "select_all", "delete_text"]
    assert actions[2] == ("delete_text", 1)


def test_clear_falls_back_to_counted_backspace_when_select_all_has_no_effect() -> None:
    # A select-all chord the platform maps to something else (e.g. Playwright's `Control+a` moving
    # the caret rather than selecting, on a host where the browser reserves `ControlOrMeta+a` for
    # that) leaves the field's content untouched by `select_all` + a single backspace. The read-back
    # `value` check catches this and finishes the job with the counted backspace run, rather than
    # reporting success after deleting at most one character.
    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "delete_text" and arg == 1:
            return  # the ineffective select-all leaves the field's content untouched
        if kind == "delete_text":
            d.screen = [_field("form.note", "")]

    ok, actions, failure = _run(
        "    - clear: { into: { id: form.note } }\n", [_field("form.note", "hello")], react=react
    )
    assert ok, failure
    assert [a[0] for a in actions] == ["tap", "select_all", "delete_text", "delete_text"]
    assert actions[2] == ("delete_text", 1)
    assert actions[3] == ("delete_text", 5)  # the counted backstop, run against the unchanged value


def test_clear_without_text_selection_falls_back_to_counted_backspace() -> None:
    # A backend with no select-all handle (e.g. the live/Appium iOS route, BE-0280) can't actuate
    # select-all-then-backspace, so `clear` falls back to backspacing the reported value's length.
    driver = _NoTextSelectionDriver(screen=[_field("form.note", "hello")])
    result = run_scenario(
        driver, load_scenarios("- name: s\n  steps:\n    - clear: { into: { id: form.note } }\n")[0]
    )
    assert result.ok, result.failure
    assert [a[0] for a in driver.actions] == ["tap", "delete_text"]
    assert driver.actions[1] == ("delete_text", 5)


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


def test_paste_focuses_the_field_then_pastes() -> None:
    ok, actions, failure = _run(
        "    - paste: { into: { id: form.note } }\n", [_field("form.note", "hello")]
    )
    assert ok, failure
    assert [a[0] for a in actions] == ["tap", "paste"]


def test_paste_needs_no_prior_selection() -> None:
    # Unlike `copy`, paste inserts at the caret: a clipboard seeded by `setClipboard` is pasted with
    # no `select` before it.
    ok, actions, failure = _run(
        "    - paste: { into: { id: form.note } }\n    - paste: { into: { id: form.note } }\n",
        [_field("form.note", "")],
    )
    assert ok, failure
    assert [a[0] for a in actions].count("paste") == 2


def test_paste_replaces_and_so_invalidates_the_selection() -> None:
    # Paste replaces the selection it lands on, so a `copy` after it has nothing selected to copy.
    ok, _actions, failure = _run(
        "    - select: { into: { id: form.note } }\n"
        "    - paste: { into: { id: form.note } }\n"
        "    - copy: {}\n",
        [_field("form.note", "hello")],
    )
    assert not ok
    assert failure is not None and "selection" in failure

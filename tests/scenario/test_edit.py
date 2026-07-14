"""Round-trip tests for the Author editor's scoped edits (BE-0261).

`apply_selector` / `apply_enrichment` parse → mutate → serialize, splicing only the changed step /
expect block. These exercise the formats the old string-matcher mishandled — flow-style steps,
comments between steps, a `:`/`#` selector value, two scenarios in one file — asserting the right
step/scenario is mutated and everything else (comments, other scenarios) survives, and that the
result always re-parses.
"""

from __future__ import annotations

import pytest

from bajutsu.scenario import load_scenario_file, load_scenarios
from bajutsu.scenario.edit import EditError, apply_enrichment, apply_selector


def _step_action(text: str, scenario: str, index: int) -> tuple[str, object]:
    """The (alias, fields) of the index-th step of the named scenario, via the model."""
    scn = next(s for s in load_scenarios(text) if s.name == scenario)
    dumped = scn.steps[index].model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
    alias = next(iter(dumped))
    return alias, dumped[alias]


# --- apply_selector ---------------------------------------------------------------------------


def test_apply_selector_flow_style_step() -> None:
    text = "- name: s\n  steps:\n    - tap: { id: old.button }\n"
    out = apply_selector(text, "s", 0, {"id": "new.button"})
    assert _step_action(out, "s", 0) == ("tap", {"id": "new.button"})


def test_apply_selector_preserves_comment_between_steps() -> None:
    text = "- name: s\n  steps:\n    - tap: { id: a }   # keep this comment\n    - tap: { id: b }\n"
    out = apply_selector(text, "s", 1, {"id": "b2"})
    assert "# keep this comment" in out
    assert _step_action(out, "s", 0) == ("tap", {"id": "a"})
    assert _step_action(out, "s", 1) == ("tap", {"id": "b2"})


def test_apply_selector_value_with_colon_and_hash() -> None:
    # A selector value carrying YAML-significant characters must survive a round-trip — the old
    # hand-quoting is exactly what this replaces.
    text = "- name: s\n  steps:\n    - tap: { id: a }\n"
    weird = "ns.item:42#frag"
    out = apply_selector(text, "s", 0, {"id": weird})
    assert _step_action(out, "s", 0) == ("tap", {"id": weird})


def test_apply_selector_targets_named_scenario_only() -> None:
    text = (
        "- name: first\n"
        "  steps:\n"
        "    - tap: { id: a }\n"
        "- name: second\n"
        "  steps:\n"
        "    - tap: { id: a }\n"
    )
    out = apply_selector(text, "second", 0, {"id": "changed"})
    assert _step_action(out, "first", 0) == ("tap", {"id": "a"})
    assert _step_action(out, "second", 0) == ("tap", {"id": "changed"})


def test_apply_selector_type_preserves_text() -> None:
    text = '- name: s\n  steps:\n    - type: { into: { label: Old }, text: "hello" }\n'
    out = apply_selector(text, "s", 0, {"label": "Email", "index": 0})
    assert _step_action(out, "s", 0) == (
        "type",
        {"text": "hello", "into": {"label": "Email", "index": 0}},
    )


def test_apply_selector_longpress_keeps_sibling_fields() -> None:
    text = "- name: s\n  steps:\n    - longPress: { sel: { id: a }, duration: 2.0 }\n"
    out = apply_selector(text, "s", 0, {"id": "b"})
    alias, fields = _step_action(out, "s", 0)
    assert alias == "longPress"
    assert fields["sel"] == {"id": "b"}
    assert fields["duration"] == 2.0


def test_apply_selector_block_style_scenario_survives() -> None:
    text = "- name: s\n  steps:\n    - tap:\n        id: old\n    - back: {}\n"
    out = apply_selector(text, "s", 0, {"id": "new"})
    assert _step_action(out, "s", 0) == ("tap", {"id": "new"})
    assert _step_action(out, "s", 1)[0] == "back"


def test_apply_selector_preserves_step_modifiers() -> None:
    # A step's `name` modifier spans a second line of the same mapping; the span must cover it and
    # the re-serialized step must keep it.
    text = (
        "- name: s\n  steps:\n    - tap: { id: old }\n      name: the login tap\n    - back: {}\n"
    )
    out = apply_selector(text, "s", 0, {"id": "new"})
    scn = next(s for s in load_scenarios(out) if s.name == "s")
    assert scn.steps[0].name == "the login tap"
    assert scn.steps[0].tap is not None
    assert scn.steps[0].tap.first_id() == "new"
    assert scn.steps[1].back is not None


def test_apply_selector_mapping_form_file() -> None:
    text = "description: a file\nscenarios:\n  - name: s\n    steps:\n      - tap: { id: a }\n"
    out = apply_selector(text, "s", 0, {"id": "b"})
    assert load_scenario_file(out).description == "a file"
    assert _step_action(out, "s", 0) == ("tap", {"id": "b"})


def test_apply_selector_rejects_unsupported_action() -> None:
    text = "- name: s\n  steps:\n    - back: {}\n"
    with pytest.raises(EditError, match="cannot apply a selector to a 'back' step"):
        apply_selector(text, "s", 0, {"id": "a"})


def test_apply_selector_rejects_out_of_range_index() -> None:
    text = "- name: s\n  steps:\n    - tap: { id: a }\n"
    with pytest.raises(EditError, match="out of range"):
        apply_selector(text, "s", 5, {"id": "b"})


def test_apply_selector_rejects_unknown_scenario() -> None:
    text = "- name: s\n  steps:\n    - tap: { id: a }\n"
    with pytest.raises(EditError, match="not found"):
        apply_selector(text, "nope", 0, {"id": "b"})


# --- apply_enrichment -------------------------------------------------------------------------


def test_enrichment_appends_settle_and_creates_expect() -> None:
    text = "- name: s\n  steps:\n    - tap: { id: a }\n"
    out = apply_enrichment(
        text,
        "s",
        expect=[{"exists": {"sel": {"id": "z"}}}],
        settle={"wait": {"for": {"id": "spinner"}, "timeout": 5}},
    )
    scn = next(s for s in load_scenarios(out) if s.name == "s")
    # The settle wait is the last step; the assertion landed in expect.
    assert scn.steps[-1].wait is not None
    assert len(scn.steps) == 2
    assert len(scn.expect) == 1


def test_enrichment_replaces_existing_expect() -> None:
    text = (
        "- name: s\n"
        "  steps:\n"
        "    - tap: { id: a }   # a comment\n"
        "  expect:\n"
        "    - exists: { sel: { id: old } }\n"
    )
    out = apply_enrichment(text, "s", expect=[{"exists": {"sel": {"id": "new"}}}], settle=None)
    assert "# a comment" in out
    scn = next(s for s in load_scenarios(out) if s.name == "s")
    assert len(scn.expect) == 1
    assert scn.expect[0].exists is not None
    assert scn.expect[0].exists.sel.first_id() == "new"


def test_enrichment_leaves_other_scenarios_untouched() -> None:
    text = (
        "- name: first\n"
        "  steps:\n"
        "    - tap: { id: a }   # first comment\n"
        "- name: second\n"
        "  steps:\n"
        "    - tap: { id: b }\n"
    )
    out = apply_enrichment(text, "second", expect=[{"exists": {"sel": {"id": "z"}}}], settle=None)
    assert "# first comment" in out
    first = next(s for s in load_scenarios(out) if s.name == "first")
    assert not first.expect
    second = next(s for s in load_scenarios(out) if s.name == "second")
    assert len(second.expect) == 1


def test_enrichment_settle_only_keeps_expect() -> None:
    text = (
        "- name: s\n"
        "  steps:\n"
        "    - tap: { id: a }\n"
        "  expect:\n"
        "    - exists: { sel: { id: keep } }\n"
    )
    out = apply_enrichment(
        text, "s", expect=[], settle={"wait": {"for": {"id": "sp"}, "timeout": 3}}
    )
    scn = next(s for s in load_scenarios(out) if s.name == "s")
    assert scn.steps[-1].wait is not None
    assert scn.expect[0].exists is not None
    assert scn.expect[0].exists.sel.first_id() == "keep"


def test_enrichment_settle_ordered_before_existing_expect() -> None:
    # Regression: the settle wait must land in steps, not inside the expect block.
    text = (
        "- name: s\n"
        "  steps:\n"
        "    - tap: { id: a }\n"
        "  expect:\n"
        "    - exists: { sel: { id: keep } }\n"
    )
    out = apply_enrichment(
        text,
        "s",
        expect=[{"exists": {"sel": {"id": "z"}}}],
        settle={"wait": {"for": {"id": "sp"}, "timeout": 5}},
    )
    scn = next(s for s in load_scenarios(out) if s.name == "s")
    assert len(scn.steps) == 2
    assert scn.steps[1].wait is not None
    assert len(scn.expect) == 1
    assert scn.expect[0].exists.sel.first_id() == "z"


def test_enrichment_rejects_unknown_scenario() -> None:
    text = "- name: s\n  steps:\n    - tap: { id: a }\n"
    with pytest.raises(EditError, match="not found"):
        apply_enrichment(text, "nope", expect=[{"exists": {"sel": {"id": "z"}}}], settle=None)

"""Tests for scripts/sync_roadmap_topic_labels.py (BE-0156).

The workflow labels a roadmap-item PR with a ``topic:<key>`` matching its ``Topic``. The whole
decision — which labels to add/remove given how a PR changed item files — is a pure function of the
changed-file list plus each file's current and previous ``Topic``, and that purity is what these
tests pin. The ``git show`` / working-tree reads are injected as callbacks, so nothing touches the
network or a real checkout.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sync_roadmap_topic_labels.py"
_spec = importlib.util.spec_from_file_location("sync_roadmap_topic_labels", _MODULE_PATH)
assert _spec and _spec.loader
labels = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = labels
_spec.loader.exec_module(labels)


def _item_text(topic: str, *, status: str = "Proposal") -> str:
    """A minimal BE item file body carrying a Topic (and Status) in the fenced metadata block."""
    return (
        "# BE-9001 — demo item\n\n"
        "<!-- BE-METADATA -->\n"
        "| Field | Value |\n|---|---|\n"
        f"| Status | **{status}** |\n"
        f"| Topic | {topic} |\n"
        "<!-- /BE-METADATA -->\n\n"
        "## Introduction\n\nwhy.\n"
    )


# Two real topics from the canonical list; their labels are derived from the same mapping the code
# uses (`label_for_topic`), so the expected labels can't drift from the source of truth if a topic's
# key changes. The `assert` fails loudly if either topic name is ever removed/renamed in TOPICS.
_KNOWN_TOPIC = "Contributor workflow"
_OTHER_TOPIC = "Integration & automation"
assert _KNOWN_TOPIC in labels.TOPIC_KEY_BY_NAME and _OTHER_TOPIC in labels.TOPIC_KEY_BY_NAME
_KNOWN_LABEL = labels.label_for_topic(_KNOWN_TOPIC)
_OTHER_LABEL = labels.label_for_topic(_OTHER_TOPIC)


def _changed(status: str, filename: str, previous_filename: str | None = None) -> object:
    return labels.ChangedFile(status=status, filename=filename, previous_filename=previous_filename)


# --- scope ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "roadmaps/BE-0156-roadmap-topic-label-sync/BE-0156-roadmap-topic-label-sync.md",
        "roadmaps/BE-0001-x/BE-0001-x.md",
        "roadmaps/BE-XXXX-x/BE-XXXX-x.md",  # placeholder is still an item file
    ],
)
def test_scoped_item_file_accepts_flat_english_item_files(path: str) -> None:
    assert labels.is_scoped_item_file(path)


@pytest.mark.parametrize(
    "path",
    [
        "roadmaps/BE-0156-x/BE-0156-x-ja.md",  # the Japanese mirror
        "roadmaps/README.md",  # the generated index
        "roadmaps/BE-0156-x/notes.md",  # not the item file
        "roadmaps/not-an-item/not-an-item.md",  # not a BE dir
        "roadmaps/proposals/BE-0156-x/BE-0156-x.md",  # a retired status-folder path (now 4 parts)
        "scripts/build_roadmap_index.py",  # unrelated path
    ],
)
def test_scoped_item_file_rejects_everything_else(path: str) -> None:
    assert not labels.is_scoped_item_file(path)


# --- topic / label mapping --------------------------------------------------------


def test_topic_of_reads_the_metadata_field() -> None:
    assert labels.topic_of(_item_text(_KNOWN_TOPIC)) == _KNOWN_TOPIC


def test_topic_of_is_none_when_absent() -> None:
    assert labels.topic_of(None) is None
    assert labels.topic_of("# BE-9001 — no metadata\n") is None


def test_label_for_topic_maps_known_and_rejects_unknown() -> None:
    assert labels.label_for_topic(_KNOWN_TOPIC) == _KNOWN_LABEL
    assert labels.label_for_topic("Not A Real Topic") is None


# --- path -> topic mapping --------------------------------------------------------


def test_path_topic_labels_maps_a_mapped_tree() -> None:
    assert labels.path_topic_labels("bajutsu/mcp/server.py") == {"topic:mcp"}
    assert labels.path_topic_labels("demos/showcase/app.py") == {"topic:dogfood"}


def test_path_topic_labels_is_empty_for_an_unmapped_path() -> None:
    # docs and dependency lockfiles have no canonical topic, so they take no path label.
    assert labels.path_topic_labels("docs/architecture.md") == set()
    assert labels.path_topic_labels("uv.lock") == set()


def test_path_topic_labels_exact_and_suffix_rules() -> None:
    assert labels.path_topic_labels("Makefile") == {"topic:contribution"}
    assert labels.path_topic_labels("BajutsuKit/Sources/x.swift") == {"topic:platform"}


def test_path_topic_labels_maps_the_record_modules() -> None:
    # The record feature is a pair of top-level modules plus a CLI command; touching any of them
    # lands topic:record so a reviewer can filter record work off the PR list.
    assert labels.path_topic_labels("bajutsu/record.py") == {"topic:record"}
    assert labels.path_topic_labels("bajutsu/record_capture.py") == {"topic:record"}
    assert labels.path_topic_labels("bajutsu/cli/commands/record.py") == {"topic:record"}


def test_path_topic_rules_reference_only_real_topic_keys() -> None:
    # Mirrors the module's import-time assertion: a rule can only name a key TOPICS defines.
    assert labels._PATH_RULE_KEYS.issubset(labels.TOPIC_KEY_BY_NAME.values())


# --- compute_actions (the reconciling decision) -----------------------------------


def _actions(
    entries: list[object],
    head: dict[str, str],
    base: dict[str, str] | None = None,
    current: set[str] | None = None,
) -> object:
    """Run compute_actions with in-memory head/base reads and the PR's current topic labels."""
    base = base or {}
    return labels.compute_actions(
        entries, lambda p: head.get(p), lambda p: base.get(p), current or set()
    )


def test_added_item_emits_a_single_add() -> None:
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions([_changed("added", path)], {path: _item_text(_KNOWN_TOPIC)})
    assert (plan.adds, plan.removes, plan.warnings) == ([_KNOWN_LABEL], [], [])


def test_added_item_with_unknown_topic_warns_and_emits_nothing() -> None:
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions([_changed("added", path)], {path: _item_text("Bogus Topic")})
    assert plan.adds == [] and plan.removes == []
    assert len(plan.warnings) == 1 and "Bogus Topic" in plan.warnings[0]


def test_modified_with_unchanged_topic_is_a_noop() -> None:
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions(
        [_changed("modified", path)],
        {path: _item_text(_KNOWN_TOPIC)},
        {path: _item_text(_KNOWN_TOPIC)},
    )
    assert (plan.adds, plan.removes, plan.warnings) == ([], [], [])


def test_modified_with_changed_topic_relabels() -> None:
    # base topic KNOWN is on the PR; the head changes it to OTHER -> add OTHER, drop the stale KNOWN.
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions(
        [_changed("modified", path)],
        {path: _item_text(_OTHER_TOPIC)},
        {path: _item_text(_KNOWN_TOPIC)},
        current={_KNOWN_LABEL},
    )
    assert plan.adds == [_OTHER_LABEL] and plan.removes == [_KNOWN_LABEL]


def test_renamed_reads_the_base_topic_from_the_previous_path() -> None:
    old = "roadmaps/BE-9001-old/BE-9001-old.md"
    new = "roadmaps/BE-9001-new/BE-9001-new.md"
    # The new Topic lives at the new (head) path; the old Topic must be read at the previous path.
    plan = _actions(
        [_changed("renamed", new, previous_filename=old)],
        {new: _item_text(_OTHER_TOPIC)},
        {old: _item_text(_KNOWN_TOPIC)},
        current={_KNOWN_LABEL},
    )
    assert plan.adds == [_OTHER_LABEL] and plan.removes == [_KNOWN_LABEL]


def test_pure_slug_rename_without_topic_change_is_a_noop() -> None:
    old = "roadmaps/BE-9001-old/BE-9001-old.md"
    new = "roadmaps/BE-9001-new/BE-9001-new.md"
    plan = _actions(
        [_changed("renamed", new, previous_filename=old)],
        {new: _item_text(_KNOWN_TOPIC)},
        {old: _item_text(_KNOWN_TOPIC)},
    )
    assert plan.adds == [] and plan.removes == []


def test_out_of_scope_entries_are_ignored() -> None:
    ja = "roadmaps/BE-9001-x/BE-9001-x-ja.md"  # the Japanese mirror
    index = "roadmaps/README.md"  # the generated index
    plan = _actions(
        [_changed("added", ja), _changed("modified", index)],
        {ja: _item_text(_KNOWN_TOPIC), index: "x"},
    )
    assert plan.adds == [] and plan.removes == []


def test_implemented_item_is_skipped_by_status() -> None:
    # BE-0159 retired the implemented/ folder; a shipped item is excluded by reading its Status, not
    # its path. An added Implemented item (e.g. an item that ships with its code) gets no label.
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions(
        [_changed("added", path)], {path: _item_text(_KNOWN_TOPIC, status="Implemented")}
    )
    assert plan.adds == [] and plan.removes == []


def test_shipping_an_item_reconciles_its_label_off() -> None:
    # A PR that flips an item to Implemented at head: it drops out of the desired set (Status skip),
    # so a topic label an earlier push added is reconciled away.
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions(
        [_changed("modified", path)],
        {path: _item_text(_KNOWN_TOPIC, status="Implemented")},
        {path: _item_text(_KNOWN_TOPIC)},
        current={_KNOWN_LABEL},
    )
    assert plan.adds == [] and plan.removes == [_KNOWN_LABEL]


def test_two_items_sharing_a_new_topic_dedup_to_one_add() -> None:
    a = "roadmaps/BE-9001-a/BE-9001-a.md"
    b = "roadmaps/BE-9002-b/BE-9002-b.md"
    plan = _actions(
        [_changed("added", a), _changed("added", b)],
        {a: _item_text(_KNOWN_TOPIC), b: _item_text(_KNOWN_TOPIC)},
    )
    assert plan.adds == [_KNOWN_LABEL] and plan.removes == []


def test_desired_already_matches_current_is_a_noop() -> None:
    # A later push whose head still calls for the label the PR already carries does nothing.
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions(
        [_changed("added", path)], {path: _item_text(_KNOWN_TOPIC)}, current={_KNOWN_LABEL}
    )
    assert plan.adds == [] and plan.removes == []


def test_reclassified_new_item_replaces_its_stale_label() -> None:
    # Regression: a *new* item (status stays `added` across pushes) reclassified mid-review must not
    # accumulate both labels. Push 1 added it as KNOWN (PR now carries KNOWN); push 2 makes it OTHER.
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions(
        [_changed("added", path)], {path: _item_text(_OTHER_TOPIC)}, current={_KNOWN_LABEL}
    )
    assert plan.adds == [_OTHER_LABEL] and plan.removes == [_KNOWN_LABEL]


def test_reverted_topic_drops_out_of_the_diff_and_the_label_is_removed() -> None:
    # Regression: after a Topic edit is reverted the file matches base, so GitHub drops it from the
    # PR diff entirely (no entry). The label added earlier must still be reconciled away.
    plan = _actions([], {}, current={_OTHER_LABEL})
    assert plan.adds == [] and plan.removes == [_OTHER_LABEL]


def test_a_sibling_still_holding_a_topic_keeps_its_label() -> None:
    # One new item claims KNOWN; a second item is reclassified KNOWN -> OTHER. Both KNOWN (from the
    # new item) and OTHER (the reclassified item's new topic) are desired; nothing is removed.
    added = "roadmaps/BE-9001-new/BE-9001-new.md"
    moved = "roadmaps/BE-9002-moved/BE-9002-moved.md"
    plan = _actions(
        [_changed("added", added), _changed("modified", moved)],
        {added: _item_text(_KNOWN_TOPIC), moved: _item_text(_OTHER_TOPIC)},
        {moved: _item_text(_KNOWN_TOPIC)},
        current={_KNOWN_LABEL},
    )
    assert plan.adds == [_OTHER_LABEL] and plan.removes == []


# --- path labels flow through the same reconcile ----------------------------------


def test_path_only_pr_gets_a_path_topic_label() -> None:
    # A PR touching no roadmap item still lands an area label from its paths alone.
    plan = _actions([_changed("modified", "demos/showcase/app.py")], {})
    assert plan.adds == ["topic:dogfood"] and plan.removes == []


def test_path_label_is_status_agnostic() -> None:
    # Deleting files under a mapped tree is still a change in that area.
    plan = _actions([_changed("removed", "bajutsu/mcp/old.py")], {})
    assert plan.adds == ["topic:mcp"] and plan.removes == []


def test_roadmap_item_and_path_labels_union() -> None:
    # The two sources union: the item's topic plus every touched tree's topic.
    item = "roadmaps/BE-9001-x/BE-9001-x.md"
    plan = _actions(
        [_changed("added", item), _changed("modified", "demos/x/app.py")],
        {item: _item_text(_KNOWN_TOPIC)},
    )
    assert plan.adds == sorted([_KNOWN_LABEL, "topic:dogfood"]) and plan.removes == []


def test_path_label_reconciled_off_when_its_tree_leaves_the_diff() -> None:
    # A later push drops the mapped file (only an unmapped docs path remains): the path label the PR
    # carried is reconciled away, the same convergence the roadmap source relies on.
    plan = _actions([_changed("modified", "docs/x.md")], {}, current={"topic:dogfood"})
    assert plan.adds == [] and plan.removes == ["topic:dogfood"]


# --- parsing / main seam ----------------------------------------------------------


def test_parse_changed_files_reads_status_filename_and_previous() -> None:
    payload = json.dumps(
        [
            {"status": "added", "filename": "a.md"},
            {"status": "renamed", "filename": "new.md", "previous_filename": "old.md"},
        ]
    )
    parsed = labels.parse_changed_files(payload)
    assert parsed[0] == labels.ChangedFile("added", "a.md", None)
    assert parsed[1] == labels.ChangedFile("renamed", "new.md", "old.md")


def test_parse_current_labels_keeps_only_topic_labels() -> None:
    assert labels.parse_current_labels("bug, topic:mcp , documentation,topic:security") == {
        "topic:mcp",
        "topic:security",
    }
    assert labels.parse_current_labels("") == set()


def test_main_prints_actions_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = "roadmaps/BE-9001-x/BE-9001-x.md"
    monkeypatch.setattr(labels, "read_working_tree", lambda p: _item_text(_KNOWN_TOPIC))
    monkeypatch.setattr(labels, "git_show", lambda base, p: None)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps([{"status": "added", "filename": path}])),
    )
    assert labels.main([]) == 0
    out = capsys.readouterr().out
    assert out.strip() == f"add {_KNOWN_LABEL}"

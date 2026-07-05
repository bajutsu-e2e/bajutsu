#!/usr/bin/env python3
"""Keep an open roadmap item's PR labeled with a ``topic:<key>`` label matching its ``Topic`` (BE-0156).

The roadmap groups every item under one of the canonical topics in ``build_roadmap_index``'s
``TOPICS`` tuple, but that grouping is invisible on the GitHub PR list where reviewers triage. A
``topic:<key>`` label surfaces it there — for a brand new item, and for a ``Topic`` edit on an
already-numbered item.

Labeling is **reconciling, not a diff replay.** GitHub's ``pulls/{pr}/files`` is the PR's whole
base→head diff, so from it the *desired* topic-label set for the PR is a pure function of the
current head: the topic of every added item, plus the new topic of every item whose ``Topic``
changed against the base. Reconciling that desired set against the PR's current ``topic:*`` labels
(add the missing, remove the extra) converges on every push — where a naive "remove old, add new"
delta would not: it would leave both labels behind when a *new* item is reclassified mid-review
(the file stays ``added``, so a delta only ever adds), or strip the wrong one after a revert. A
prose edit that leaves ``Topic`` unchanged contributes nothing, so it never labels a PR on its own.

The decision is a pure function of the changed-file list, each file's current (working tree) and
previous (base commit) ``Topic``, and the PR's current topic labels — fully unit-testable without
the network. The ``git show`` against the base commit is the one seam, injected as a callback. The
``gh`` label mutations live in the workflow (``roadmap-topic-labels.yml``); this script only prints
``add <label>`` / ``remove <label>`` lines for it to execute.

Scope mirrors BE-0109's shipped boundary: only an English item file (``roadmaps/BE-NNNN-<slug>/
BE-NNNN-<slug>.md``) counts, and an item whose head ``Status`` is ``Implemented`` is skipped — a
shipped item has no open PR left to triage. Since BE-0159 flattened the per-``Status`` folders, that
exclusion reads the item's ``Status`` from its head content rather than keying on an ``implemented/``
path prefix.

Usage::

    BASE_SHA=<sha> CURRENT_TOPIC_LABELS=<csv> gh api ... pulls/<pr>/files \\
      | python scripts/sync_roadmap_topic_labels.py     # prints add/remove label actions

It reads the changed-file JSON (GitHub's ``pulls/{pr}/files`` shape) from stdin and reads the base
``Topic`` via ``git show`` — so it runs in the workflow, never inside ``make check``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_roadmap_index import TOPIC_KEY_BY_NAME, metadata_fields
from roadmap_ids import is_item_dir

# The Status that gets no topic label (BE-0156): a shipped item has no open PR left to triage, the
# shipped-side boundary BE-0109 also draws. Since BE-0159 retired the per-Status folders this is read
# from the item's head metadata, not an ``implemented/`` path — every other Status (Proposal /
# In progress / Proposal (deferred)) is in scope, since a deferred item can still be un-deferred.
SHIPPED_STATUS = "Implemented"
# ``topic:<key>`` — the same key ``build_roadmap_index`` already assigns each topic, so a 24th topic
# never needs a separate label-mapping update. Short enough to scan in the PR list's label row.
LABEL_PREFIX = "topic:"
# GitHub file-change statuses that may carry a ``Topic`` edit to relabel. ``added`` is handled on its
# own (it has no base to diff). ``removed`` / ``copied`` / ``unchanged`` never denote a Topic change
# on an in-scope item, so they contribute nothing.
EDIT_STATUSES = frozenset({"modified", "renamed", "changed"})


@dataclass(frozen=True)
class ChangedFile:
    """One entry from GitHub's ``pulls/{pr}/files`` list, trimmed to what labeling needs."""

    status: str
    filename: str
    previous_filename: str | None  # only set for a rename; the pre-rename path


@dataclass(frozen=True)
class Plan:
    """The labels to add and remove on the PR, plus any non-fatal warnings to surface."""

    adds: list[str]
    removes: list[str]
    warnings: list[str]


def is_scoped_item_file(path: str) -> bool:
    """Whether ``path`` is an English BE item file in the flat roadmap tree.

    True only for ``roadmaps/BE-*-<slug>/BE-*-<slug>.md`` — the non-``-ja`` file per item directory
    (BE-0159 flattened the per-Status folders, so an item is one level below ``roadmaps/``). This
    naturally excludes the Japanese mirror (its name ends ``-ja.md``, not ``<dir>.md``) and the
    generated index pages. The shipped-item exclusion is Status-based (see :func:`desired_labels`),
    not a path check, since there is no longer an ``implemented/`` folder to key on.
    """
    parts = PurePosixPath(path).parts
    if len(parts) != 3:
        return False
    root, item_dir, filename = parts
    return root == "roadmaps" and is_item_dir(item_dir) and filename == f"{item_dir}.md"


def topic_of(text: str | None) -> str | None:
    """The item's ``Topic`` value from its file body, or ``None`` if the text/field is absent."""
    if text is None:
        return None
    return metadata_fields(text).get("Topic")


def label_for_topic(topic: str) -> str | None:
    """The ``topic:<key>`` label for a canonical topic, or ``None`` if the topic is unknown."""
    key = TOPIC_KEY_BY_NAME.get(topic)
    return f"{LABEL_PREFIX}{key}" if key else None


def _warn_unknown(topic: str, path: str) -> str:
    return (
        f"::warning file={path}::Topic {topic!r} is not in the canonical topic list; "
        "no topic label emitted for it (add it to TOPICS to enable labeling)."
    )


def desired_labels(
    entries: list[ChangedFile],
    read_head: Callable[[str], str | None],
    read_base: Callable[[str], str | None],
) -> tuple[set[str], list[str]]:
    """The ``topic:*`` labels the PR *should* carry, given its cumulative base→head diff.

    A label is desired when an in-scope item file is added (its head topic), or is modified/renamed
    with a head ``Topic`` that differs from the base (its new topic). An edit that leaves ``Topic``
    unchanged contributes nothing, so a prose-only edit never labels a PR on its own. An item whose
    head ``Status`` is ``Implemented`` is skipped — a shipped item has no open PR left to triage
    (BE-0159 made this a Status read rather than an ``implemented/`` folder check).

    Returns:
        The desired label set and any ``::warning::`` lines for a head ``Topic`` outside the
        canonical list (its file is skipped, never failing the PR).
    """
    desired: set[str] = set()
    warnings: list[str] = []
    for entry in entries:
        if not is_scoped_item_file(entry.filename):
            continue
        head_text = read_head(entry.filename)
        if head_text is None:
            continue
        head_fields = metadata_fields(head_text)
        if head_fields.get("Status") == SHIPPED_STATUS:
            continue  # shipped item — no open PR left to triage
        head_topic = head_fields.get("Topic")
        if entry.status in EDIT_STATUSES:
            old_path = entry.previous_filename or entry.filename
            if head_topic == topic_of(read_base(old_path)):
                continue  # the common case: an edit that doesn't touch Topic
        elif entry.status != "added":
            continue  # removed / copied / unchanged — never a Topic change to label
        if head_topic is None:
            continue
        label = label_for_topic(head_topic)
        if label is None:
            warnings.append(_warn_unknown(head_topic, entry.filename))
            continue
        desired.add(label)
    return desired, warnings


def compute_actions(
    entries: list[ChangedFile],
    read_head: Callable[[str], str | None],
    read_base: Callable[[str], str | None],
    current_labels: set[str],
) -> Plan:
    """Reconcile the PR's current ``topic:*`` labels toward the set its head diff calls for.

    Args:
        entries: the PR's changed files (GitHub's ``pulls/{pr}/files`` shape).
        read_head: read a path's current (PR-head working tree) content, ``None`` if unreadable.
        read_base: read a path's content at the PR base commit, ``None`` if unreadable.
        current_labels: the ``topic:*`` labels already on the PR.

    Returns:
        The labels to add (desired but absent) and remove (present but no longer desired), plus any
        ``::warning::`` lines. Because the desired set is recomputed from the whole base→head diff
        each run, this reconciliation converges on every push regardless of prior label state.
    """
    desired, warnings = desired_labels(entries, read_head, read_base)
    return Plan(
        adds=sorted(desired - current_labels),
        removes=sorted(current_labels - desired),
        warnings=warnings,
    )


def parse_changed_files(payload: str) -> list[ChangedFile]:
    """Parse GitHub's ``pulls/{pr}/files`` JSON array into :class:`ChangedFile` entries."""
    return [
        ChangedFile(
            status=item["status"],
            filename=item["filename"],
            previous_filename=item.get("previous_filename"),
        )
        for item in json.loads(payload)
    ]


def parse_current_labels(csv: str) -> set[str]:
    """Parse the comma-separated current PR labels, keeping only the ``topic:*`` ones."""
    return {name for raw in csv.split(",") if (name := raw.strip()).startswith(LABEL_PREFIX)}


def read_working_tree(path: str) -> str | None:
    """Read a checked-out file's text, ``None`` if it is absent/unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def git_show(base_sha: str, path: str) -> str | None:
    """Read ``path`` at the base commit via ``git show``, ``None`` if it did not exist there.

    An empty ``base_sha`` returns ``None`` rather than letting ``git show :path`` resolve the
    staged index blob — a missing base means "no previous Topic", not the working index.
    """
    if not base_sha:
        return None
    result = subprocess.run(
        ["git", "show", f"{base_sha}:{path}"], capture_output=True, encoding="utf-8"
    )
    return result.stdout if result.returncode == 0 else None


def main(argv: list[str]) -> int:
    base_sha = os.environ.get("BASE_SHA", "")
    current = parse_current_labels(os.environ.get("CURRENT_TOPIC_LABELS", ""))
    entries = parse_changed_files(sys.stdin.read())
    plan = compute_actions(
        entries, read_working_tree, lambda path: git_show(base_sha, path), current
    )
    for warning in plan.warnings:
        print(warning, file=sys.stderr)
    for label in plan.adds:
        print(f"add {label}")
    for label in plan.removes:
        print(f"remove {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

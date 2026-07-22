"""Tests for the roadmap metadata loader (scripts/build_roadmap_index.py).

The loader reads each BE item's own metadata into a plain in-memory model — consumed by the roadmap
dashboard generator and a handful of other roadmap tools. These tests pin the pure pieces — metadata
parsing, status-to-bucket classification, and duplicate-id detection — plus loading the real,
committed roadmap tree end to end.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_roadmap_index.py"
_spec = importlib.util.spec_from_file_location("build_roadmap_index", _MODULE_PATH)
assert _spec and _spec.loader
bri = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bri  # let dataclass resolve annotations during exec
_spec.loader.exec_module(bri)


EN_FILE = """\
**English** · [日本語](BE-0029-visual-regression-assertions-ja.md)

# BE-0029 — Visual-regression assertions

* Proposal: [BE-0029](BE-0029-visual-regression-assertions.md)
* Status: **Implemented**
* Topic: Verification & coverage
* Origin: Both

## Introduction
"""

JA_FILE = """\
[English](BE-0029-visual-regression-assertions.md) · **日本語**

# BE-0029 — ビジュアル回帰アサーション

* 提案: [BE-0029](BE-0029-visual-regression-assertions-ja.md)
* 状態: **実装済み**
* トピック: 検証とカバレッジ
* 由来: 両社

## はじめに
"""


FENCED_EN_FILE = """\
**English** · [日本語](BE-0029-visual-regression-assertions-ja.md)

# BE-0029 — Visual-regression assertions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0029](BE-0029-visual-regression-assertions.md) |
| Status | **Implemented** |
| Topic | Verification & coverage |
| Origin | Both |
<!-- /BE-METADATA -->

## Detailed design

A same-shaped table in the body must not be read as metadata:

| Field | Value |
|---|---|
| Status | not-metadata |
"""


def test_parse_metadata_reads_fenced_table() -> None:
    title, fields = bri.parse_metadata(FENCED_EN_FILE)
    assert title == "Visual-regression assertions"
    # The fenced data rows are read; the header row and the body table are not.
    assert fields["Status"] == "Implemented"
    assert fields["Topic"] == "Verification & coverage"
    assert fields["Origin"] == "Both"
    assert "Field" not in fields


def test_parse_metadata_reads_title_and_fields() -> None:
    title, fields = bri.parse_metadata(EN_FILE)
    assert title == "Visual-regression assertions"
    assert fields["Status"] == "Implemented"
    assert fields["Topic"] == "Verification & coverage"
    assert fields["Origin"] == "Both"


def test_parse_metadata_japanese_fields() -> None:
    title, fields = bri.parse_metadata(JA_FILE)
    assert title == "ビジュアル回帰アサーション"
    assert fields["状態"] == "実装済み"
    assert fields["由来"] == "両社"


def test_bucket_derives_classification_from_status() -> None:
    # Status is the lone lifecycle field; the bucket is derived from it, not a hand-set Track
    # (retired in BE-0078) — so folder and bucket can never disagree.
    assert bri.bucket("Implemented") == "Implemented"
    assert bri.bucket("In progress") == "In progress"
    assert bri.bucket("Proposal") == "Proposals"
    assert bri.bucket("Proposal (deferred)") == "Deferred"


def test_bucket_rejects_unknown_status() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown status"):
        bri.bucket("Something else")


def test_tracking_issue_url_is_a_pure_function_of_the_id() -> None:
    url = bri.tracking_issue_url("BE-0139")
    assert url.startswith("https://github.com/bajutsu-e2e/bajutsu/issues")
    assert "roadmap-tracking" in url
    assert "BE-0139" in url


def test_duplicate_ids_flags_collisions(tmp_path: Path) -> None:
    """duplicate_ids reports any BE number shared by two item directories."""
    roadmap = tmp_path / "roadmaps"
    for name in ("BE-0045-foo", "BE-0045-bar", "BE-0046-baz"):
        (roadmap / name).mkdir(parents=True)
    dupes = bri.duplicate_ids(roadmap)
    assert set(dupes) == {"BE-0045"}
    assert sorted(dupes["BE-0045"]) == ["BE-0045-bar", "BE-0045-foo"]


def test_load_items_rejects_duplicate_ids(tmp_path: Path) -> None:
    """The loader refuses a tree with a duplicate id instead of loading two items for it."""
    import pytest

    roadmap = tmp_path / "roadmaps"
    for name in ("BE-0045-foo", "BE-0045-bar"):
        (roadmap / name).mkdir(parents=True)
    with pytest.raises(ValueError, match="duplicate BE IDs"):
        bri.load_items(roadmap)


def test_no_duplicate_be_ids() -> None:
    """The gate: no two roadmap items share a BE id (IDs are unique and permanent)."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    assert bri.duplicate_ids(roadmap) == {}, "duplicate BE IDs found in roadmaps/"


def test_load_items_loads_the_committed_roadmap_tree() -> None:
    """The gate: every committed item parses, and its Topic maps to a known section (BE-0074)."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    items = bri.load_items(roadmap)
    assert items, "expected at least one roadmap item"
    for item in items:
        assert item.bucket in dict(bri.BUCKETS)
        assert item.topic in bri.KNOWN_TOPICS
        assert "en" in item.by_lang and "ja" in item.by_lang

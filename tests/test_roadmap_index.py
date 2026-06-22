"""Tests for the roadmap index generator (scripts/build_roadmap_index.py).

The generator regenerates the marker-delimited table bodies in roadmaps/README.md and
README-ja.md from each BE item's own metadata, so a roadmap PR only touches its own directory
(BE-0043). These tests pin the pure pieces — metadata parsing, per-language row rendering, and
marker-region replacement — plus an end-to-end build over a temporary roadmap tree, and finally
assert the committed index is already up to date (the same check the gate runs).
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
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: Both

## Introduction
"""

JA_FILE = """\
[English](BE-0029-visual-regression-assertions.md) · **日本語**

# BE-0029 — ビジュアル回帰アサーション

* 提案: [BE-0029](BE-0029-visual-regression-assertions-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: 両社

## はじめに
"""


FENCED_EN_FILE = """\
**English** · [日本語](BE-0029-visual-regression-assertions-ja.md)

# BE-0029 — Visual-regression assertions

<!-- BE-METADATA -->
| Proposal | [BE-0029](BE-0029-visual-regression-assertions.md) |
| Status | **Implemented** |
| Track | [Accepted](../README.md#accepted) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | Both |
<!-- /BE-METADATA -->

## Detailed design

A same-shaped row in the body must not be read as metadata:

| Status | not-metadata |
"""


def test_parse_metadata_reads_fenced_block() -> None:
    title, fields = bri.parse_metadata(FENCED_EN_FILE)
    assert title == "Visual-regression assertions"
    # The fenced rows are read; the same-shaped body row outside the fence is not.
    assert fields["Status"] == "Implemented"
    assert fields["Topic"] == "Candidates from competitive research (MagicPod / Autify)"
    assert fields["Origin"] == "Both"


def test_parse_metadata_reads_title_and_fields() -> None:
    title, fields = bri.parse_metadata(EN_FILE)
    assert title == "Visual-regression assertions"
    assert fields["Status"] == "Implemented"
    assert fields["Topic"] == "Candidates from competitive research (MagicPod / Autify)"
    assert fields["Origin"] == "Both"


def test_parse_metadata_japanese_fields() -> None:
    title, fields = bri.parse_metadata(JA_FILE)
    assert title == "ビジュアル回帰アサーション"
    assert fields["状態"] == "実装済み"
    assert fields["由来"] == "両社"


def test_track_label_extracts_bracket_text() -> None:
    _, fields = bri.parse_metadata(EN_FILE)
    assert bri.track_label(fields["Track"]) == "Accepted"
    _, ja_fields = bri.parse_metadata(JA_FILE)
    assert bri.track_label(ja_fields["トラック"]) == "可決済み"


def test_status_display_english() -> None:
    assert bri.status_display("Implemented", "en") == "Implemented"
    assert bri.status_display("Accepted, in progress", "en") == "In progress"
    assert bri.status_display("Proposal", "en") == "Proposal"
    assert bri.status_display("Proposal (deferred)", "en") == "Deferred"


def test_status_display_japanese() -> None:
    assert bri.status_display("実装済み", "ja") == "実装済み"
    assert bri.status_display("可決・実装中", "ja") == "実装中"
    assert bri.status_display("提案", "ja") == "提案"
    assert bri.status_display("提案（保留）", "ja") == "保留"


def test_render_row_english_with_origin() -> None:
    entry = bri.Entry(
        id="BE-0029",
        slug="visual-regression-assertions",
        category="implemented",
        title="Visual-regression assertions",
        status="Implemented",
        origin="Both",
    )
    row = bri.render_row(entry, "en", has_origin=True)
    assert row == (
        "| [BE-0029](implemented/BE-0029-visual-regression-assertions/"
        "BE-0029-visual-regression-assertions.md) "
        "| Visual-regression assertions | Implemented | Both |"
    )


def test_render_row_english_without_origin() -> None:
    entry = bri.Entry(
        id="BE-0001",
        slug="m1-deterministic-runner",
        category="implemented",
        title="Deterministic runner (M1)",
        status="Implemented",
        origin=None,
    )
    row = bri.render_row(entry, "en", has_origin=False)
    assert row == (
        "| [BE-0001](implemented/BE-0001-m1-deterministic-runner/"
        "BE-0001-m1-deterministic-runner.md) | Deterministic runner (M1) | Implemented |"
    )


def test_render_row_japanese_links_to_ja_file() -> None:
    entry = bri.Entry(
        id="BE-0029",
        slug="visual-regression-assertions",
        category="implemented",
        title="ビジュアル回帰アサーション",
        status="実装済み",
        origin="両社",
    )
    row = bri.render_row(entry, "ja", has_origin=True)
    assert row == (
        "| [BE-0029](implemented/BE-0029-visual-regression-assertions/"
        "BE-0029-visual-regression-assertions-ja.md) "
        "| ビジュアル回帰アサーション | 実装済み | 両社 |"
    )


def test_replace_region_swaps_only_marked_body() -> None:
    text = (
        "intro prose\n"
        "| ID | Item | Status |\n"
        "|---|---|---|\n"
        "<!-- GENERATED:demo -->\n"
        "| old row |\n"
        "<!-- /GENERATED:demo -->\n"
        "trailing prose\n"
    )
    out = bri.replace_region(text, "demo", "| new row 1 |\n| new row 2 |")
    assert "old row" not in out
    assert "| new row 1 |\n| new row 2 |" in out
    # markers and surrounding prose are preserved
    assert "intro prose" in out
    assert "trailing prose" in out
    assert "<!-- GENERATED:demo -->" in out
    assert "<!-- /GENERATED:demo -->" in out


def test_replace_region_missing_marker_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="absent"):
        bri.replace_region("no markers here\n", "demo", "| x |")


def test_committed_index_is_up_to_date() -> None:
    """The gate: every committed index table already matches generated output."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    stale = bri.stale_files(roadmap)
    assert stale == [], (
        "roadmap index is out of date; run `python scripts/build_roadmap_index.py` "
        f"and commit: {stale}"
    )


def test_duplicate_ids_flags_collisions(tmp_path: Path) -> None:
    """duplicate_ids reports any BE number shared by two item directories."""
    roadmap = tmp_path / "roadmaps"
    for rel in ("implemented/BE-0045-foo", "proposals/BE-0045-bar", "proposals/BE-0046-baz"):
        (roadmap / rel).mkdir(parents=True)
    dupes = bri.duplicate_ids(roadmap)
    assert set(dupes) == {"BE-0045"}
    assert sorted(dupes["BE-0045"]) == ["implemented/BE-0045-foo", "proposals/BE-0045-bar"]


def test_load_items_rejects_duplicate_ids(tmp_path: Path) -> None:
    """The index build refuses a tree with a duplicate id instead of rendering two rows."""
    import pytest

    roadmap = tmp_path / "roadmaps"
    for rel in ("implemented/BE-0045-foo", "proposals/BE-0045-bar"):
        (roadmap / rel).mkdir(parents=True)
    with pytest.raises(ValueError, match="duplicate BE IDs"):
        bri.load_items(roadmap)


def test_no_duplicate_be_ids() -> None:
    """The gate: no two roadmap items share a BE id (IDs are unique and permanent)."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    assert bri.duplicate_ids(roadmap) == {}, "duplicate BE IDs found in roadmaps/"

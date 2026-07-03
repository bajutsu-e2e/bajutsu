"""Tests for the mechanical roadmap-drift fixer (BE-0149).

Pins the two narrow shapes it handles — a banned metadata field, a missing ``## `` section — and
proves the fixed output actually passes ``check_roadmap_format.format_problems`` (the fixer and the
checker must agree on what "fixed" means). Also pins that anything wider (a reordered or extra
heading) is left untouched, since that needs human judgment.
"""

from __future__ import annotations

from conftest import valid_roadmap_item_en as _valid_en
from conftest import valid_roadmap_item_ja as _valid_ja

from scripts.check_roadmap_format import format_problems
from scripts.fix_roadmap_drift import fix_unknown_fields_and_missing_sections


def _assert_valid_shape(text: str, *, lang: str) -> None:
    """The BE-XXXX self-reference is legitimate for a placeholder, so pass it through
    ``format_problems`` on a throwaway tree — checked directly against the file's own logic here to
    keep the test independent of any real roadmap tree."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        d = Path(tmp) / "proposals" / "BE-XXXX-a-thing"
        d.mkdir(parents=True)
        en = text if lang == "en" else _valid_en()
        ja = text if lang == "ja" else _valid_ja()
        (d / "BE-XXXX-a-thing.md").write_text(en, encoding="utf-8")
        (d / "BE-XXXX-a-thing-ja.md").write_text(ja, encoding="utf-8")
        assert format_problems(Path(tmp)) == []


def test_a_valid_file_is_returned_unchanged() -> None:
    text = _valid_en()
    assert fix_unknown_fields_and_missing_sections(text, lang="en") == text


def test_drops_the_retired_track_field() -> None:
    text = _valid_en().replace(
        "| Status | **Proposal** |\n", "| Status | **Proposal** |\n| Track | Something |\n"
    )
    fixed = fix_unknown_fields_and_missing_sections(text, lang="en")
    assert "| Track |" not in fixed
    _assert_valid_shape(fixed, lang="en")


def test_drops_the_retired_track_field_ja() -> None:
    text = _valid_ja().replace(
        "| 状態 | **提案** |\n", "| 状態 | **提案** |\n| Track | Something |\n"
    )
    fixed = fix_unknown_fields_and_missing_sections(text, lang="ja")
    assert "| Track |" not in fixed
    _assert_valid_shape(fixed, lang="ja")


def test_inserts_a_missing_progress_section_in_canonical_position() -> None:
    # The exact BE-0137/BE-0138 shape: everything present except Progress, which sits right before
    # References.
    text = _valid_en().replace("## Progress\n\nTBD\n\n", "")
    fixed = fix_unknown_fields_and_missing_sections(text, lang="en")
    assert "## Progress\n\n> Keep this current" in fixed
    assert fixed.index("## Progress") < fixed.index("## References")
    _assert_valid_shape(fixed, lang="en")


def test_inserts_a_missing_progress_section_ja() -> None:
    text = _valid_ja().replace("## 進捗\n\nTBD\n\n", "")
    fixed = fix_unknown_fields_and_missing_sections(text, lang="ja")
    assert "## 進捗\n\n> 開発の進行に合わせて" in fixed
    assert fixed.index("## 進捗") < fixed.index("## 参考")
    _assert_valid_shape(fixed, lang="ja")


def test_inserts_a_missing_section_at_the_end_when_no_later_heading_survives() -> None:
    # References missing and nothing canonical follows it — appended at EOF.
    text = _valid_en().replace("\n\n## References\n\nTBD\n", "\n")
    fixed = fix_unknown_fields_and_missing_sections(text, lang="en")
    assert fixed.rstrip("\n").endswith("## References\n\nTBD")
    _assert_valid_shape(fixed, lang="en")


def test_fixes_both_shapes_together() -> None:
    text = (
        _valid_en()
        .replace("| Status | **Proposal** |\n", "| Status | **Proposal** |\n| Track | Old |\n")
        .replace("## Progress\n\nTBD\n\n", "")
    )
    fixed = fix_unknown_fields_and_missing_sections(text, lang="en")
    assert "| Track |" not in fixed
    assert "## Progress" in fixed
    _assert_valid_shape(fixed, lang="en")


def test_leaves_a_reordered_heading_untouched() -> None:
    # Alternatives considered and Detailed design swapped — a shape wider than "just missing", so
    # the fixer must not guess at a reorder; a human handles this.
    text = _valid_en().replace(
        "## Detailed design\n\nTBD\n\n## Alternatives considered\n\nTBD",
        "## Alternatives considered\n\nTBD\n\n## Detailed design\n\nTBD",
    )
    assert fix_unknown_fields_and_missing_sections(text, lang="en") == text


def test_leaves_an_unresolvable_shape_untouched_when_no_metadata_block_exists() -> None:
    text = "no metadata block here\n"
    assert fix_unknown_fields_and_missing_sections(text, lang="en") == text

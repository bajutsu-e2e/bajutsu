"""Tests for the placeholder-aware roadmap format checker (BE-0149).

The check itself was factored out of ``tests/test_roadmap_format.py`` into
``scripts/check_roadmap_format.py`` so the merge-time allocator can self-validate before pushing to
``main`` without the test toolchain. These tests build throwaway roadmap trees to pin the property
BE-0149 adds: a ``BE-XXXX`` placeholder is checked exactly like a numbered item — the same headings,
the same ``Track`` ban and required ``Progress`` section — closing the gap that let a malformed
placeholder reach ``main`` (as BE-0137/BE-0138 did).
"""

from __future__ import annotations

from pathlib import Path

from scripts.build_roadmap_index import tracking_issue_url
from scripts.check_roadmap_format import format_problems, unresolved_be_xxxx_references

_HEADINGS = ("Introduction", "Motivation", "Detailed design", "Alternatives considered")
_HEADINGS_JA = ("はじめに", "動機", "詳細設計", "検討した代替案")


def _valid_en(id_token: str, slug: str) -> str:
    body = "\n\n".join(f"## {h}\n\nTBD" for h in _HEADINGS)
    return (
        f"**English** · [日本語]({id_token}-{slug}-ja.md)\n\n"
        f"# {id_token} — A test item\n\n"
        "<!-- BE-METADATA -->\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Proposal | [{id_token}]({id_token}-{slug}.md) |\n"
        "| Author | [@0x0c](https://github.com/0x0c) |\n"
        "| Status | **Proposal** |\n"
        f"| Tracking issue | [Search]({tracking_issue_url(id_token)}) |\n"
        "| Topic | Development infrastructure (contributor workflow) |\n"
        "<!-- /BE-METADATA -->\n\n"
        f"{body}\n\n## Progress\n\nTBD\n\n## References\n\nTBD\n"
    )


def _valid_ja(id_token: str, slug: str) -> str:
    body = "\n\n".join(f"## {h}\n\nTBD" for h in _HEADINGS_JA)
    return (
        f"[English]({id_token}-{slug}.md) · **日本語**\n\n"
        f"# {id_token} — A test item\n\n"
        "<!-- BE-METADATA -->\n"
        "| 項目 | 値 |\n"
        "|---|---|\n"
        f"| 提案 | [{id_token}]({id_token}-{slug}-ja.md) |\n"
        "| 提案者 | [@0x0c](https://github.com/0x0c) |\n"
        "| 状態 | **提案** |\n"
        f"| トラッキング Issue | [検索]({tracking_issue_url(id_token)}) |\n"
        "| トピック | 開発基盤（コントリビュータ体験） |\n"
        "<!-- /BE-METADATA -->\n\n"
        f"{body}\n\n## 進捗\n\nTBD\n\n## 参考\n\nTBD\n"
    )


def _write_item(
    roadmap: Path, id_token: str, slug: str, *, en: str | None = None, ja: str | None = None
) -> Path:
    d = roadmap / "proposals" / f"{id_token}-{slug}"
    d.mkdir(parents=True)
    (d / f"{id_token}-{slug}.md").write_text(en or _valid_en(id_token, slug), encoding="utf-8")
    (d / f"{id_token}-{slug}-ja.md").write_text(ja or _valid_ja(id_token, slug), encoding="utf-8")
    return d


def test_a_valid_placeholder_passes(tmp_path: Path) -> None:
    _write_item(tmp_path, "BE-XXXX", "a-thing")
    assert format_problems(tmp_path) == []


def test_a_valid_numbered_item_passes(tmp_path: Path) -> None:
    _write_item(tmp_path, "BE-0042", "a-thing")
    assert format_problems(tmp_path) == []


def test_placeholder_missing_progress_section_is_caught(tmp_path: Path) -> None:
    en = _valid_en("BE-XXXX", "a-thing").replace("## Progress\n\nTBD\n\n", "")
    _write_item(tmp_path, "BE-XXXX", "a-thing", en=en)
    problems = format_problems(tmp_path)
    assert any("H2 headings must be exactly" in p for p in problems), problems


def test_placeholder_with_retired_track_field_is_caught(tmp_path: Path) -> None:
    en = _valid_en("BE-XXXX", "a-thing").replace(
        "| Status | **Proposal** |\n", "| Status | **Proposal** |\n| Track | Something |\n"
    )
    _write_item(tmp_path, "BE-XXXX", "a-thing", en=en)
    problems = format_problems(tmp_path)
    assert any("unknown metadata field(s): Track" in p for p in problems), problems


def test_placeholder_with_numbered_title_is_caught(tmp_path: Path) -> None:
    # A placeholder's title must self-reference BE-XXXX, not carry a stray real id.
    en = _valid_en("BE-XXXX", "a-thing").replace(
        "# BE-XXXX — A test item", "# BE-0042 — A test item"
    )
    _write_item(tmp_path, "BE-XXXX", "a-thing", en=en)
    problems = format_problems(tmp_path)
    assert any("missing a '# BE-XXXX — <title>' H1" in p for p in problems), problems


def test_empty_tree_reports_no_items(tmp_path: Path) -> None:
    assert format_problems(tmp_path) == ["no roadmap items found"]


def test_dangling_be_xxxx_reference_in_numbered_item_is_caught(tmp_path: Path) -> None:
    en = _valid_en("BE-0042", "a-thing").replace(
        "## References\n\nTBD\n",
        "## References\n\nSee [BE-XXXX](../BE-XXXX-other/BE-XXXX-other.md)\n",
    )
    _write_item(tmp_path, "BE-0042", "a-thing", en=en)
    problems = unresolved_be_xxxx_references(tmp_path)
    assert any("BE-XXXX-other" in p for p in problems), problems


def test_placeholder_self_reference_is_exempt(tmp_path: Path) -> None:
    # A placeholder's own files self-reference BE-XXXX (header link, Proposal metadata) — legitimate
    # until CI numbers it, so they must not trip the unresolved-reference check.
    _write_item(tmp_path, "BE-XXXX", "a-thing")
    assert unresolved_be_xxxx_references(tmp_path) == []

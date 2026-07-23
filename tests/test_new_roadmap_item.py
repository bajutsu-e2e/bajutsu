"""Tests for scripts/new_roadmap_item.py — the roadmap-item scaffolder (BE-0069 A).

Scaffolds into a temporary roadmap tree (no mocks) and asserts the generated pair matches the
canonical shape the format gate (tests/test_roadmap_format.py) pins, with the literal BE-XXXX
placeholder and a handle-link Author.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "new_roadmap_item.py"
_spec = importlib.util.spec_from_file_location("new_roadmap_item", _MODULE_PATH)
assert _spec and _spec.loader
nri = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = nri
_spec.loader.exec_module(nri)

_TOPIC = "Contributor workflow"


def _scaffold(tmp_path: Path, **kw: str) -> Path:
    roadmap = tmp_path / "roadmaps"
    roadmap.mkdir()
    defaults = {"topic": _TOPIC, "status": "Proposal", "handle": "octocat"}
    return nri.scaffold(
        roadmap,
        kw.pop("slug", "demo-feature"),
        kw.pop("title", "Demo feature"),
        **{**defaults, **kw},
    )  # type: ignore[arg-type]


def test_creates_both_language_files_with_placeholder(tmp_path: Path) -> None:
    item = _scaffold(tmp_path)
    assert item.name == "BE-XXXX-demo-feature"
    assert item.parent.name == "roadmaps"  # flat layout (BE-0159): no status folder
    assert (item / "BE-XXXX-demo-feature.md").is_file()
    assert (item / "BE-XXXX-demo-feature-ja.md").is_file()


def test_english_file_has_canonical_shape(tmp_path: Path) -> None:
    text = (_scaffold(tmp_path) / "BE-XXXX-demo-feature.md").read_text(encoding="utf-8")
    assert text.startswith("**English** · [日本語](BE-XXXX-demo-feature-ja.md)\n")
    assert "# BE-XXXX — Demo feature" in text
    assert "| Proposal | [BE-XXXX](BE-XXXX-demo-feature.md) |" in text
    assert "| Author | [@octocat](https://github.com/octocat) |" in text
    assert "| Status | **Proposal** |" in text
    # Tracking issue (BE-0139): a search URL computed from the literal BE-XXXX placeholder, which
    # the CI allocator rewrites to the real id alongside the rest of the file.
    assert f"| Tracking issue | [Search]({nri._tracking_issue_url('BE-XXXX')}) |" in text
    assert f"| Topic | {_TOPIC} |" in text
    for section in (
        "Introduction",
        "Motivation",
        "Detailed design",
        "Alternatives considered",
        "References",
    ):
        assert f"## {section}\n\nTBD" in text
    # Progress (BE-0100) is seeded with its living-checklist skeleton, between Alternatives and
    # References, not a bare TBD.
    assert "## Progress\n\n> Keep this current as work proceeds." in text
    assert "- [ ] TBD — enumerate the work breakdown (MECE) here once scoped." in text
    assert (
        text.index("## Alternatives considered")
        < text.index("## Progress")
        < text.index("## References")
    )


def test_japanese_file_has_canonical_shape(tmp_path: Path) -> None:
    text = (_scaffold(tmp_path) / "BE-XXXX-demo-feature-ja.md").read_text(encoding="utf-8")
    assert text.startswith("[English](BE-XXXX-demo-feature.md) · **日本語**\n")
    assert "| 提案者 | [@octocat](https://github.com/octocat) |" in text
    assert "| 状態 | **提案** |" in text  # Proposal -> 提案
    assert f"| トラッキング Issue | [検索]({nri._tracking_issue_url('BE-XXXX')}) |" in text
    for section in ("はじめに", "動機", "詳細設計", "検討した代替案", "参考"):
        assert f"## {section}\n\nTBD" in text
    assert "## 進捗\n\n> 開発の進行に合わせて常に最新の状態に保ってください。" in text
    assert "- [ ] TBD — スコープが固まり次第、作業分解（MECE）をここに列挙します。" in text


def test_status_maps_to_japanese(tmp_path: Path) -> None:
    text = (_scaffold(tmp_path, status="In progress") / "BE-XXXX-demo-feature-ja.md").read_text(
        encoding="utf-8"
    )
    assert "| 状態 | **実装中** |" in text


def test_unknown_topic_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="unknown TOPIC"):
        _scaffold(tmp_path, topic="Not A Real Topic")


def test_non_kebab_slug_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="kebab-case"):
        _scaffold(tmp_path, slug="Bad_Slug")


def test_handle_is_stripped_of_leading_at(tmp_path: Path) -> None:
    text = (_scaffold(tmp_path, handle="@octocat") / "BE-XXXX-demo-feature.md").read_text(
        encoding="utf-8"
    )
    assert "| Author | [@octocat](https://github.com/octocat) |" in text


@pytest.mark.parametrize(
    "status", ["Proposal", "In progress", "Implemented", "Proposal (deferred)"]
)
def test_item_lands_directly_under_roadmaps_regardless_of_status(
    tmp_path: Path, status: str
) -> None:
    # BE-0159 scaffolds every new item at roadmaps/BE-XXXX-<slug>/ — Status decides only the index
    # bucket, never the directory — so the scaffold path is the same for every Status.
    item = _scaffold(tmp_path, status=status)
    assert item.parent.name == "roadmaps"
    assert item.name == "BE-XXXX-demo-feature"


def test_empty_title_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="TITLE"):
        _scaffold(tmp_path, title="")


def test_whitespace_only_title_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="TITLE"):
        _scaffold(tmp_path, title="   ")

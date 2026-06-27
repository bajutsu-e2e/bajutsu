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

_TOPIC = "Development infrastructure (contributor workflow)"


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
    assert item.parent.name == "proposals"
    assert (item / "BE-XXXX-demo-feature.md").is_file()
    assert (item / "BE-XXXX-demo-feature-ja.md").is_file()


def test_english_file_has_canonical_shape(tmp_path: Path) -> None:
    text = (_scaffold(tmp_path) / "BE-XXXX-demo-feature.md").read_text(encoding="utf-8")
    assert text.startswith("**English** · [日本語](BE-XXXX-demo-feature-ja.md)\n")
    assert "# BE-XXXX — Demo feature" in text
    assert "| Proposal | [BE-XXXX](BE-XXXX-demo-feature.md) |" in text
    assert "| Author | [@octocat](https://github.com/octocat) |" in text
    assert "| Status | **Proposal** |" in text
    assert f"| Topic | {_TOPIC} |" in text
    for section in (
        "Introduction",
        "Motivation",
        "Detailed design",
        "Alternatives considered",
        "References",
    ):
        assert f"## {section}\n\nTBD" in text


def test_japanese_file_has_canonical_shape(tmp_path: Path) -> None:
    text = (_scaffold(tmp_path) / "BE-XXXX-demo-feature-ja.md").read_text(encoding="utf-8")
    assert text.startswith("[English](BE-XXXX-demo-feature.md) · **日本語**\n")
    assert "| 提案者 | [@octocat](https://github.com/octocat) |" in text
    assert "| 状態 | **提案** |" in text  # Proposal -> 提案
    for section in ("はじめに", "動機", "詳細設計", "検討した代替案", "参考"):
        assert f"## {section}\n\nTBD" in text


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


def test_in_progress_item_lands_in_in_progress_folder(tmp_path: Path) -> None:
    # Status is the source of truth for the folder; scaffolding into proposals/ regardless of
    # Status would make the promote gate flag the item immediately after creation.
    item = _scaffold(tmp_path, status="In progress")
    assert item.parent.name == "in-progress"


def test_implemented_item_lands_in_implemented_folder(tmp_path: Path) -> None:
    item = _scaffold(tmp_path, status="Implemented")
    assert item.parent.name == "implemented"


def test_deferred_item_lands_in_deferred_folder(tmp_path: Path) -> None:
    item = _scaffold(tmp_path, status="Proposal (deferred)")
    assert item.parent.name == "deferred"


def test_empty_title_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="TITLE"):
        _scaffold(tmp_path, title="")


def test_whitespace_only_title_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="TITLE"):
        _scaffold(tmp_path, title="   ")

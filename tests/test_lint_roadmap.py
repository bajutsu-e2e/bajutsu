"""Tests for scripts/lint_roadmap.py — roadmap cross-link resolution + author handle check (BE-0069).

Operates on a temporary roadmap tree (no mocks): real item directories and files under tmp_path,
so the link resolution and the in-place ``--fix`` rewrite are exercised exactly as on the real tree.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lint_roadmap.py"
_spec = importlib.util.spec_from_file_location("lint_roadmap", _MODULE_PATH)
assert _spec and _spec.loader
lr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lr
_spec.loader.exec_module(lr)

_AUTHOR = "[@octocat](https://github.com/octocat)"


def _write_item(roadmap: Path, name: str, *, body: str = "", author: str = _AUTHOR) -> Path:
    """Create a BE item directory (both language files) with a metadata block and optional body.

    Since BE-0159 every item is a sibling directory directly under ``roadmaps/`` — item-to-item
    links are ``../BE-NNNN-<slug>/BE-NNNN-<slug>.md``, with no status-folder segment.
    """
    item = roadmap / name
    item.mkdir(parents=True)
    for suffix, status in (("", "Proposal"), ("-ja", "提案")):
        meta = (
            f"# {name} — demo\n\n"
            "<!-- BE-METADATA -->\n| Field | Value |\n|---|---|\n"
            f"| Author | {author} |\n| Status | **{status}** |\n<!-- /BE-METADATA -->\n\n"
        )
        # The body (any cross-link under test) goes in the English file only, so a link counts once.
        (item / f"{name}{suffix}.md").write_text(
            meta + (body if suffix == "" else ""), encoding="utf-8"
        )
    return item


def test_resolving_links_pass(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    # A correct flat sibling link from one item to another.
    link = "see [BE-9002](../BE-9002-target/BE-9002-target.md)\n"
    _write_item(roadmap, "BE-9001-source", body=link)
    assert lr.broken_links(roadmap) == []
    assert lr.author_problems(roadmap) == []


def test_broken_link_detected_with_suggestion(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    # A stale link carrying the retired status-folder segment: it no longer resolves post-flatten.
    link = "see [BE-9002](../../proposals/BE-9002-target/BE-9002-target.md)\n"
    _write_item(roadmap, "BE-9001-source", body=link)

    broken = lr.broken_links(roadmap)
    assert len(broken) == 1
    assert broken[0].suggestion == "../BE-9002-target/BE-9002-target.md"


def test_fix_rewrites_broken_link(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    link = "see [BE-9002](../../proposals/BE-9002-target/BE-9002-target.md)\n"
    source = _write_item(roadmap, "BE-9001-source", body=link)

    assert lr.fix_links(roadmap) == 1
    assert lr.broken_links(roadmap) == []
    text = (source / "BE-9001-source.md").read_text(encoding="utf-8")
    assert "../BE-9002-target/BE-9002-target.md" in text


def test_fix_preserves_anchor_fragment(tmp_path: Path) -> None:
    # A link with a #fragment must be matched and rewritten verbatim (the path resolves the same).
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    link = "see [BE-9002](../../proposals/BE-9002-target/BE-9002-target.md#motivation)\n"
    source = _write_item(roadmap, "BE-9001-source", body=link)

    assert lr.fix_links(roadmap) == 1
    text = (source / "BE-9001-source.md").read_text(encoding="utf-8")
    assert "../BE-9002-target/BE-9002-target.md#motivation" in text
    assert lr.broken_links(roadmap) == []


def test_fix_count_matches_occurrences_not_matches(tmp_path: Path) -> None:
    # The same broken link twice in one file counts as two rewrites, not one — and not four
    # (str.replace fixes both at once; the count must reflect actual occurrences).
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    link = "../../proposals/BE-9002-target/BE-9002-target.md"
    body = f"first [a]({link}) and second [b]({link})\n"
    _write_item(roadmap, "BE-9001-source", body=body)

    assert lr.fix_links(roadmap) == 2
    assert lr.broken_links(roadmap) == []


def test_stale_slug_resolved_by_id(tmp_path: Path) -> None:
    # The item was renamed (slug changed) but the link still carries the old slug; the BE id
    # alone must still resolve it, rewriting to the current slug.
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-new-slug")
    link = "see [BE-9002](../BE-9002-old-slug/BE-9002-old-slug.md)\n"
    _write_item(roadmap, "BE-9001-source", body=link)

    broken = lr.broken_links(roadmap)
    assert len(broken) == 1
    assert broken[0].suggestion == "../BE-9002-new-slug/BE-9002-new-slug.md"
    assert lr.fix_links(roadmap) == 1
    assert lr.broken_links(roadmap) == []


def test_dangling_reference_is_reported_not_fixed(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    link = "see [BE-9999](../BE-9999-ghost/BE-9999-ghost.md)\n"  # no such item anywhere
    _write_item(roadmap, "BE-9001-source", body=link)

    broken = lr.broken_links(roadmap)
    assert len(broken) == 1 and broken[0].suggestion is None
    assert lr.fix_links(roadmap) == 0  # nothing to point it at — left untouched


def test_author_not_handle_link_is_flagged(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9001-plain", author="Jane Doe")
    problems = lr.author_problems(roadmap)
    assert any("Author is not a handle link" in p for p in problems)
    # The same item with a proper handle link is clean.
    _write_item(roadmap, "BE-9002-ok")
    assert all("BE-9002-ok" not in p for p in lr.author_problems(roadmap))


def test_bilingual_header_link_is_not_flagged(tmp_path: Path) -> None:
    # The same-directory bilingual header link (no directory component) is not an item cross-link.
    roadmap = tmp_path / "roadmaps"
    header = "**English** · [日本語](BE-9001-source-ja.md)\n"
    _write_item(roadmap, "BE-9001-source", body=header)
    assert lr.broken_links(roadmap) == []


def _write_doc(repo: Path, rel: str, body: str) -> Path:
    """Create a Markdown file at ``repo/rel`` (a docs page or a top-level README/CLAUDE)."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_docs_link_to_moved_item_detected_and_fixed(tmp_path: Path) -> None:
    # A docs page links an item by a retired status-folder path (BE-0096): it no longer resolves
    # post-flatten, and the docs side is checked and repaired the same as item bodies.
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    doc = _write_doc(
        tmp_path,
        "docs/guide.md",
        "see [BE-9002](../roadmaps/implemented/BE-9002-target/BE-9002-target.md)\n",
    )

    broken = lr.docs_broken_links(roadmap)
    assert len(broken) == 1
    assert broken[0].suggestion == "../roadmaps/BE-9002-target/BE-9002-target.md"

    assert lr.fix_links(roadmap) == 1
    assert lr.docs_broken_links(roadmap) == []
    assert "../roadmaps/BE-9002-target/BE-9002-target.md" in doc.read_text(encoding="utf-8")


def test_docs_resolving_link_passes(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    _write_doc(
        tmp_path,
        "docs/guide.md",
        "see [BE-9002](../roadmaps/BE-9002-target/BE-9002-target.md)\n",
    )
    assert lr.docs_broken_links(roadmap) == []


def test_docs_dangling_link_reported_not_fixed(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_doc(
        tmp_path,
        "docs/guide.md",
        "see [BE-9999](../roadmaps/BE-9999-ghost/BE-9999-ghost.md)\n",
    )
    broken = lr.docs_broken_links(roadmap)
    assert len(broken) == 1 and broken[0].suggestion is None
    assert lr.fix_links(roadmap) == 0


def test_top_level_readme_and_claude_md_covered(tmp_path: Path) -> None:
    # The repo-root README.md / README.ja.md / CLAUDE.md link to items too, with no leading ``../``.
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    for name in ("README.md", "README.ja.md", "CLAUDE.md"):
        _write_doc(
            tmp_path,
            name,
            f"link [BE-9002](roadmaps/implemented/BE-9002-target/BE-9002-target.md) in {name}\n",
        )

    broken = lr.docs_broken_links(roadmap)
    assert {b.source.name for b in broken} == {"README.md", "README.ja.md", "CLAUDE.md"}
    assert all(b.suggestion == "roadmaps/BE-9002-target/BE-9002-target.md" for b in broken)
    assert lr.fix_links(roadmap) == 3


def test_generated_dashboard_page_is_excluded_from_the_docs_scan(tmp_path: Path) -> None:
    """``docs/api/roadmap.md`` (build_roadmap_dashboard.py) is never linted here.

    It is a build artifact regenerated from live metadata on every docs build (never committed, so
    it cannot rot on promotion the way authored prose can), and it deliberately links items via an
    absolute GitHub blob URL rather than a repo-relative path — a convention this checker's
    relative-file resolution was never meant to validate.
    """
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-9002-target")
    _write_doc(
        tmp_path,
        "docs/api/roadmap.md",
        "[BE-9002](https://github.com/bajutsu-e2e/bajutsu/blob/main/"
        "roadmaps/BE-9002-target/BE-9002-target.md)\n",
    )
    assert lr.docs_broken_links(roadmap) == []
    assert lr.docs_broken_links(roadmap) == []

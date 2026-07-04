"""Tests for the pure parts of the stale-roadmap-PR re-checker (BE-0149).

The network-calling glue (listing PRs, fetching content, pushing a fix branch, opening/commenting a
PR) isn't covered here — it calls ``gh``/``git`` and never runs inside ``make check``, the same
carve-out ``sync_roadmap_tracking_issues.py`` documents. These pin the parts that don't touch the
network: building the overlay tree, detecting drift against it, and computing mechanical fixes.
"""

from __future__ import annotations

from pathlib import Path

from conftest import valid_roadmap_item_en as _valid_en

from scripts.check_stale_roadmap_prs import build_overlay, compute_fixes, detect_drift


def _seed_main_roadmap(roadmap: Path) -> None:
    """A minimal but complete main-shaped roadmaps/ tree: one conformant numbered item."""
    d = roadmap / "BE-0042-existing"
    d.mkdir(parents=True)
    (d / "BE-0042-existing.md").write_text(_valid_en("BE-0042", "existing"), encoding="utf-8")
    (d / "BE-0042-existing-ja.md").write_text("placeholder ja body\n", encoding="utf-8")


def test_build_overlay_adds_a_new_placeholder(tmp_path: Path) -> None:
    main_roadmap = tmp_path / "main-roadmaps"
    _seed_main_roadmap(main_roadmap)
    dest = tmp_path / "overlay"

    en = _valid_en("BE-XXXX", "new-thing")
    build_overlay(
        main_roadmap,
        dest,
        {
            "roadmaps/BE-XXXX-new-thing/BE-XXXX-new-thing.md": en,
            "roadmaps/BE-XXXX-new-thing/BE-XXXX-new-thing-ja.md": "ja\n",
        },
    )

    assert (dest / "BE-0042-existing" / "BE-0042-existing.md").is_file()
    added = dest / "BE-XXXX-new-thing" / "BE-XXXX-new-thing.md"
    assert added.read_text(encoding="utf-8") == en


def test_build_overlay_deletes_a_removed_file(tmp_path: Path) -> None:
    main_roadmap = tmp_path / "main-roadmaps"
    _seed_main_roadmap(main_roadmap)
    dest = tmp_path / "overlay"

    build_overlay(main_roadmap, dest, {"roadmaps/BE-0042-existing/BE-0042-existing.md": None})

    assert not (dest / "BE-0042-existing" / "BE-0042-existing.md").exists()


def test_detect_drift_is_clean_for_a_conformant_overlay(tmp_path: Path) -> None:
    main_roadmap = tmp_path / "main-roadmaps"
    _seed_main_roadmap(main_roadmap)

    en = _valid_en("BE-XXXX", "fine-thing")
    problems = detect_drift(
        main_roadmap,
        {
            "roadmaps/BE-XXXX-fine-thing/BE-XXXX-fine-thing.md": en,
            "roadmaps/BE-XXXX-fine-thing/BE-XXXX-fine-thing-ja.md": "placeholder ja body\n",
        },
    )
    # Only the ja file (a deliberately unrealistic stub) trips checks; the point is the en file,
    # already conformant, contributes no problems of its own.
    assert not any("BE-XXXX:" in p and "ja" not in p for p in problems)


def test_detect_drift_catches_a_missing_progress_section(tmp_path: Path) -> None:
    main_roadmap = tmp_path / "main-roadmaps"
    _seed_main_roadmap(main_roadmap)

    en = _valid_en("BE-XXXX", "stale-thing").replace("## Progress\n\nTBD\n\n", "")
    problems = detect_drift(
        main_roadmap,
        {
            "roadmaps/BE-XXXX-stale-thing/BE-XXXX-stale-thing.md": en,
            "roadmaps/BE-XXXX-stale-thing/BE-XXXX-stale-thing-ja.md": "placeholder ja body\n",
        },
    )
    assert any("H2 headings must be exactly" in p for p in problems)


def test_compute_fixes_only_includes_changed_files() -> None:
    conformant = _valid_en("BE-XXXX", "a")
    drifted = _valid_en("BE-XXXX", "b").replace("## Progress\n\nTBD\n\n", "")
    fixes = compute_fixes(
        {
            "roadmaps/BE-XXXX-a/BE-XXXX-a.md": conformant,
            "roadmaps/BE-XXXX-b/BE-XXXX-b.md": drifted,
            "roadmaps/BE-XXXX-c/BE-XXXX-c.md": None,  # deleted — skipped
        }
    )
    assert set(fixes) == {"roadmaps/BE-XXXX-b/BE-XXXX-b.md"}
    assert "## Progress" in fixes["roadmaps/BE-XXXX-b/BE-XXXX-b.md"]

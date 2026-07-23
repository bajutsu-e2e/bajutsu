"""Lint roadmap (BE) item files: cross-link resolution + author handle-link (BE-0069).

The *shape* of each item (bilingual pair, metadata block, the five sections) is pinned by
``tests/test_roadmap_format.py`` (BE-0074). This is the companion that validates what a single
item's shape cannot:

- **Cross-link resolution.** Every markdown link to an item's file must resolve to a file that
  exists — both the links *between* items and the links *into* ``roadmaps/`` from ``docs/`` and the
  top-level ``README*`` / ``CLAUDE.md`` (BE-0096). Since BE-0159 every item lives at a permanent flat
  ``roadmaps/BE-NNNN-<slug>/`` path, so promotion no longer moves a file and cannot rot a link; this
  still catches the plain mistakes a fixed layout doesn't prevent — a typo'd slug, a link to a
  renamed item, or a reference to an id that never existed.
- **Author handle-link.** The ``Author`` (``提案者``) value must be ``[@handle](https://github.com/handle)``.

``--fix`` rewrites a broken item link to its target's current ``BE-NNNN-slug`` path (located by
directory name), across roadmap item bodies *and* ``docs/`` / the top-level files. A link whose
target item does not exist anywhere is a genuine dangling reference: reported, never "fixed".
Runnable mid-edit via ``make lint-roadmap`` (in ``make check``).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Import the shared id-shape predicate whether this file is run as ``python3 scripts/…`` (scripts/
# already on the path) or loaded under its bare name by a test — add scripts/ so the sibling import
# resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roadmap_ids import is_numbered_dir, iter_item_dirs

ROADMAP = Path(__file__).resolve().parent.parent / "roadmaps"
# Markdown outside roadmaps/ that links *into* it and so rots on promotion the same way item bodies
# do (BE-0096): every page under docs/, plus the repo-root README/CLAUDE files.
TOP_LEVEL_DOCS = ("README.md", "README.ja.md", "CLAUDE.md")
# Excluded from that scan: the generated roadmap dashboard (build_roadmap_dashboard.py). It is a
# build artifact regenerated from live metadata on every docs build (never committed, so it cannot
# rot on promotion the way authored prose can), and it deliberately links every item via an absolute
# GitHub blob URL rather than a repo-relative path — a convention this checker's relative-file
# resolution was never meant to validate.
_EXCLUDED_DOCS = frozenset({"docs/api/roadmap.md"})

# A markdown link target that names another item's file: a ``BE-NNNN-slug/BE-NNNN-slug[-ja].md``
# path (always reached via ``../`` from a sibling item). The bilingual header link is a bare
# same-directory ``BE-NNNN-slug-ja.md`` (no directory component) and so is intentionally not matched.
_ITEM_LINK_RE = re.compile(
    r"\]\((?P<target>[^)\s]*BE-\d{4}-[^)/]+/BE-\d{4}-[^)/]+\.md)(?P<frag>#[^)]*)?\)"
)
_DIR_RE = re.compile(r"(BE-\d{4}-[^/)]+)/(BE-\d{4}-[^/)]+\.md)$")
_AUTHOR_RE = re.compile(r"^\|\s*(?:Author|提案者)\s*\|\s*(?P<val>.+?)\s*\|\s*$", re.MULTILINE)
_HANDLE_RE = re.compile(r"^\[@[^\]]+\]\(https://github\.com/[^)]+\)$")


@dataclass(frozen=True)
class BrokenLink:
    """One unresolved item-to-item link found in a roadmap file."""

    source: Path  # the file containing the link, relative to the repo root
    target: str  # the link target exactly as written
    suggestion: str | None  # the corrected relative target, or None when the item doesn't exist


def _item_dirs(roadmap: Path) -> dict[str, Path]:
    """Map each item's ``BE-NNNN-slug`` directory name to its current absolute path.

    Every item lives at a flat ``roadmaps/BE-NNNN-<slug>/`` path (BE-0159), so a link resolves
    against the item's permanent location and ``--fix`` rewrites a stale one to that flat path.
    """
    return {d.name: d for d in iter_item_dirs(roadmap) if is_numbered_dir(d.name)}


def _dirs_by_id(item_dirs: dict[str, Path]) -> dict[str, Path]:
    """Map each ``BE-NNNN`` id to its directory, when the id is unambiguous (it always is —
    ids are unique). Lets a link with a stale *slug* still resolve to the right item."""
    by_id: dict[str, list[Path]] = {}
    for name, path in item_dirs.items():
        by_id.setdefault(name[:7], []).append(path)  # "BE-NNNN" is the first 7 chars
    return {be_id: paths[0] for be_id, paths in by_id.items() if len(paths) == 1}


def _item_files(roadmap: Path) -> list[Path]:
    return sorted(p for d in _item_dirs(roadmap).values() for p in d.glob("*.md"))


def _docs_files(repo: Path) -> list[Path]:
    """Markdown that may link into ``roadmaps/``: every page under ``docs/`` plus the top-level
    ``README*`` / ``CLAUDE.md`` (BE-0096). These rot on promotion just like item bodies do."""
    files = sorted((repo / "docs").rglob("*.md"))
    files.extend(p for name in TOP_LEVEL_DOCS if (p := repo / name).is_file())
    return [p for p in files if p.relative_to(repo).as_posix() not in _EXCLUDED_DOCS]


def _suggest(
    source: Path, target: str, item_dirs: dict[str, Path], dirs_by_id: dict[str, Path]
) -> str | None:
    """The correct relative target for a broken item link, or None if the item doesn't exist.

    Resolves by the exact ``BE-NNNN-slug`` directory first; failing that, by the ``BE-NNNN`` id
    alone, so a link carrying a stale slug (the item was renamed) still points at the right item.
    """
    match = _DIR_RE.search(target)
    if match is None:
        return None
    dir_name, file_name = match.group(1), match.group(2)
    item_dir = item_dirs.get(dir_name) or dirs_by_id.get(dir_name[:7])
    if item_dir is None:
        return None  # no item with that id exists anywhere — genuinely dangling
    # Rebuild the file name from the item's real slug, keeping the link's language (``-ja`` or not).
    lang_suffix = "-ja.md" if file_name.endswith("-ja.md") else ".md"
    item_file = item_dir / f"{item_dir.name}{lang_suffix}"
    if not item_file.is_file():
        return None
    # POSIX form so a fix produces forward-slash links that work on GitHub and every OS, even when
    # the linter runs on Windows (os.path.relpath would emit backslashes there).
    return Path(os.path.relpath(item_file, source.parent)).as_posix()


def _broken_in(
    sources: list[Path], item_dirs: dict[str, Path], dirs_by_id: dict[str, Path]
) -> list[BrokenLink]:
    """Every item link in ``sources`` that does not resolve to an existing file."""
    broken: list[BrokenLink] = []
    for source in sources:
        for m in _ITEM_LINK_RE.finditer(source.read_text(encoding="utf-8")):
            target = m.group("target")  # the path, without any #fragment
            if (source.parent / target).is_file():
                continue
            frag = m.group("frag") or ""
            suggestion = _suggest(source, target, item_dirs, dirs_by_id)
            # Carry any #fragment through on both sides, so an anchored link (`](path#frag)`) is
            # matched and rewritten verbatim — the path resolves the same with or without it.
            broken.append(
                BrokenLink(
                    source=source,
                    target=target + frag,
                    suggestion=(suggestion + frag) if suggestion is not None else None,
                )
            )
    return broken


def broken_links(roadmap: Path) -> list[BrokenLink]:
    """Every item-to-item link that does not resolve to an existing file, across all items."""
    item_dirs = _item_dirs(roadmap)
    return _broken_in(_item_files(roadmap), item_dirs, _dirs_by_id(item_dirs))


def docs_broken_links(roadmap: Path, repo: Path | None = None) -> list[BrokenLink]:
    """Every ``docs/`` (or top-level ``README*`` / ``CLAUDE.md``) link into ``roadmaps/`` that does
    not resolve (BE-0096). ``repo`` defaults to the parent of ``roadmap``."""
    item_dirs = _item_dirs(roadmap)
    return _broken_in(_docs_files(repo or roadmap.parent), item_dirs, _dirs_by_id(item_dirs))


def author_problems(roadmap: Path) -> list[str]:
    """Items whose ``Author`` value is not a GitHub-handle link."""
    problems: list[str] = []
    for source in _item_files(roadmap):
        for m in _AUTHOR_RE.finditer(source.read_text(encoding="utf-8")):
            if not _HANDLE_RE.match(m.group("val")):
                rel = source.relative_to(roadmap.parent)
                problems.append(f"{rel}: Author is not a handle link: {m.group('val')!r}")
    return problems


def fix_links(roadmap: Path) -> int:
    """Rewrite every fixable broken item link to its target's current path, in place.

    Returns the number of links rewritten. A dangling reference (target item absent) is left
    untouched — there is nothing to point it at.
    """
    fixed = 0
    # Per file, map each broken target to its fix. A dict dedupes a target that occurs N times, so
    # the rewrite + count happen once per distinct link (counting actual occurrences), not per match.
    by_source: dict[Path, dict[str, str]] = {}
    for link in broken_links(roadmap) + docs_broken_links(roadmap):
        if link.suggestion is not None:
            by_source.setdefault(link.source, {})[link.target] = link.suggestion
    for source, repls in by_source.items():
        text = source.read_text(encoding="utf-8")
        for target, suggestion in repls.items():
            occurrences = text.count(f"]({target})")
            if occurrences:
                text = text.replace(f"]({target})", f"]({suggestion})")
                fixed += occurrences
        source.write_text(text, encoding="utf-8")
    return fixed


def _problems(roadmap: Path) -> list[str]:
    problems: list[str] = []
    for link in broken_links(roadmap) + docs_broken_links(roadmap):
        rel = link.source.relative_to(ROADMAP.parent)
        if link.suggestion is None:
            problems.append(f"{rel}: link to a non-existent item: {link.target!r}")
        else:
            problems.append(f"{rel}: broken link {link.target!r} -> should be {link.suggestion!r}")
    problems.extend(author_problems(roadmap))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite broken item links to the target's current path",
    )
    args = parser.parse_args(argv)

    if args.fix:
        count = fix_links(ROADMAP)
        print(f"lint-roadmap: rewrote {count} broken link(s)")

    problems = _problems(ROADMAP)
    if problems:
        print("lint-roadmap: found problems:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        if any("non-existent item" not in p for p in problems) and args.fix:
            print(
                "  (run without --fix to see only the remaining, unfixable problems)",
                file=sys.stderr,
            )
        return 1
    print("lint-roadmap: all item links resolve and authors are handle links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

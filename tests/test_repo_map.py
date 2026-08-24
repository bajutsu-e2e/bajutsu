"""Tests for the repository map (scripts/repo_map.py).

The map prints one line per docs page, per package and top-level module, or per heading of one
file, so a session can pick what to open instead of searching for it. Nothing is committed, so the
property worth pinning is that the map matches the tree it was derived from: these tests build a
temporary tree and check each mode's rows, the summary extraction the docs mode depends on, and
the fenced-block handling that keeps a shell comment out of the heading map.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "repo_map.py"
_spec = importlib.util.spec_from_file_location("repo_map", _MODULE_PATH)
assert _spec and _spec.loader
rm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rm
_spec.loader.exec_module(rm)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_summary_reads_a_leading_block_quote(tmp_path: Path) -> None:
    """Most docs pages put their one-line description in a quote right after the H1."""
    _write(
        tmp_path / "docs" / "a.md",
        "**English**\n\n# Title\n\n> The canonical answer to what this page covers.\n\n## Next\n",
    )

    (row,) = rm.iter_docs(tmp_path / "docs")

    assert row.detail == "The canonical answer to what this page covers."


def test_summary_skips_the_related_navigation_line(tmp_path: Path) -> None:
    """A page whose quote is absent must not report its cross-link line as a summary."""
    _write(
        tmp_path / "docs" / "b.md",
        "# Title\n\nRelated: [cli](cli.md) · [concepts](concepts.md)\n\nThe real first sentence.\n",
    )

    (row,) = rm.iter_docs(tmp_path / "docs")

    assert row.detail == "The real first sentence."


def test_summary_skips_a_fenced_block(tmp_path: Path) -> None:
    """A page opening with an example reports the prose after it, not the code."""
    _write(tmp_path / "docs" / "c.md", "# Title\n\n```bash\nmake check\n```\n\nWhat it does.\n")

    (row,) = rm.iter_docs(tmp_path / "docs")

    assert row.detail == "What it does."


def test_summary_is_empty_without_prose(tmp_path: Path) -> None:
    """A page with nothing but headings gets an empty summary rather than a wrong one."""
    _write(tmp_path / "docs" / "d.md", "# Title\n\n## Only headings\n")

    (row,) = rm.iter_docs(tmp_path / "docs")

    assert row.detail == ""


def test_docs_row_carries_path_lines_and_title(tmp_path: Path) -> None:
    """Each docs row names where the page is, how long it is, and what it is called."""
    _write(tmp_path / "docs" / "sub" / "e.md", "# Driver abstraction\n\nProse.\n")

    (row,) = rm.iter_docs(tmp_path / "docs")

    assert row.path.endswith("docs/sub/e.md")
    assert row.size == "3"
    assert row.name == "Driver abstraction"


def test_docs_title_falls_back_to_the_file_stem(tmp_path: Path) -> None:
    """A page with no H1 is still findable by name."""
    _write(tmp_path / "docs" / "index.md", "Some landing content.\n")

    (row,) = rm.iter_docs(tmp_path / "docs")

    assert row.name == "index"


def test_headings_report_line_and_span(tmp_path: Path) -> None:
    """A heading's span is the distance to the next heading — the range to read."""
    page = _write(tmp_path / "p.md", "# One\n\na\nb\n\n## Two\n\nc\n")

    rows = rm.iter_headings(page)

    assert [(row.path.split(":")[-1], row.size, row.detail) for row in rows] == [
        ("1", "5 lines", "One"),
        ("6", "3 lines", "Two"),
    ]


def test_headings_ignore_a_comment_inside_a_fence(tmp_path: Path) -> None:
    """A `#` inside an example is a shell comment; counting it would misreport every span."""
    page = _write(
        tmp_path / "q.md", "# Real\n\n```bash\n# not a heading\nmake check\n```\n\n## Also\n"
    )

    rows = rm.iter_headings(page)

    assert [row.detail for row in rows] == ["Real", "Also"]


def test_headings_record_the_level(tmp_path: Path) -> None:
    """The level tells a reader how deep a heading sits without opening the file."""
    page = _write(tmp_path / "r.md", "# A\n\n### C\n")

    assert [row.name for row in rm.iter_headings(page)] == ["#", "###"]


def test_code_map_aggregates_a_package_and_details_a_module(tmp_path: Path) -> None:
    """The code map answers "which area owns this", at two levels rather than 313 file rows."""
    pkg = tmp_path / "bajutsu"
    _write(pkg / "__init__.py", "")
    _write(pkg / "top.py", "class Alpha:\n    pass\n\n\ndef beta() -> None:\n    pass\n")
    _write(pkg / "drivers" / "__init__.py", "")
    _write(pkg / "drivers" / "adb.py", "x = 1\n")
    _write(pkg / "drivers" / "base.py", "y = 2\n")

    rows = {row.path.split("bajutsu/", 1)[1]: row for row in rm.iter_code(pkg)}

    assert rows["drivers/"].size == "3 files, 2 lines"  # adb, base, and __init__
    assert rows["drivers/"].detail == "adb, base"
    assert rows["top.py"].detail == "Alpha, beta"


def test_code_map_skips_a_directory_with_no_modules(tmp_path: Path) -> None:
    """A directory holding only templates is not a code area worth a row."""
    pkg = tmp_path / "bajutsu"
    _write(pkg / "__init__.py", "")
    (pkg / "templates").mkdir()

    assert [row.path for row in rm.iter_code(pkg)] == []


def test_code_map_survives_an_unparseable_module(tmp_path: Path) -> None:
    """One broken file must not hide the rest of the map."""
    pkg = tmp_path / "bajutsu"
    _write(pkg / "__init__.py", "")
    _write(pkg / "broken.py", "def (\n")

    (row,) = rm.iter_code(pkg)

    assert row.path.endswith("bajutsu/broken.py")
    assert row.detail == ""


def test_join_names_names_the_total_when_it_truncates() -> None:
    """A silently truncated list reads as complete, so the row states how many there are."""
    names = [f"name_{n:03d}" for n in range(40)]

    joined = rm._join_names(names)

    assert joined.endswith("… (40 in all)")
    assert "name_000" in joined


def test_join_names_leaves_a_short_list_whole() -> None:
    """A list that fits is printed in full, with no ellipsis to second-guess."""
    assert rm._join_names(["a", "b"]) == "a, b"


def test_filter_rows_is_case_insensitive(tmp_path: Path) -> None:
    """A keyword narrows the map without the caller matching the tree's capitalization."""
    _write(tmp_path / "docs" / "drivers.md", "# Driver abstraction\n\nProse.\n")
    _write(tmp_path / "docs" / "cli.md", "# Command line\n\nProse.\n")

    rows = rm.filter_rows(rm.iter_docs(tmp_path / "docs"), "DRIVER")

    assert [row.name for row in rows] == ["Driver abstraction"]


def test_a_gitignored_page_is_left_out(tmp_path: Path) -> None:
    """`docs/api/roadmap.md` is built by `make docs`; listing it would make the map depend on
    whether a docs build had run."""
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("docs/generated.md\n", encoding="utf-8")
    _write(tmp_path / "docs" / "real.md", "# Real\n\nProse.\n")
    _write(tmp_path / "docs" / "generated.md", "# Built\n\nProse.\n")

    rows = rm.iter_docs(tmp_path / "docs")

    assert [row.name for row in rows] == ["Real"]


def test_an_untracked_page_is_still_listed(tmp_path: Path) -> None:
    """A page being drafted right now is not ignored, so it must stay findable."""
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    _write(tmp_path / "docs" / "draft.md", "# Draft\n\nProse.\n")

    assert [row.name for row in rm.iter_docs(tmp_path / "docs")] == ["Draft"]


def test_main_requires_a_mode(capsys) -> None:
    """Asking for no map at all is a usage error, not an empty table."""
    with pytest.raises(SystemExit):
        rm.main([])


def test_main_prints_a_table_and_is_deterministic(tmp_path: Path, capsys) -> None:
    """The same tree prints the same bytes, so the map can never drift between two runs."""
    _write(tmp_path / "docs" / "a.md", "# A\n\nProse.\n")

    assert rm.main(["--docs", "--docs-root", str(tmp_path / "docs")]) == 0
    first = capsys.readouterr().out
    assert rm.main(["--docs", "--docs-root", str(tmp_path / "docs")]) == 0

    assert capsys.readouterr().out == first
    assert first.splitlines()[0] == "| Path | Lines | Title | Summary |"


def test_main_reports_a_missing_file_without_a_traceback(tmp_path: Path, capsys) -> None:
    """A bad --headings path fails with a message a caller can act on."""
    code = rm.main(["--headings", str(tmp_path / "nope.md")])

    assert code != 0
    assert "nope.md" in capsys.readouterr().err

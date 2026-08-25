"""Tests for the architecture module-table check (scripts/lint_module_map.py).

The table in `docs/architecture.md` is hand-written role prose, so nothing generates it; what the
gate can enforce is that its row set still matches `bajutsu/`. These tests build a temporary
package and table, then pin each rule — a row pointing at nothing, an unmentioned subpackage, an
unmentioned top-level module, and an allowlist entry whose module is gone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lint_module_map.py"
_spec = importlib.util.spec_from_file_location("lint_module_map", _MODULE_PATH)
assert _spec and _spec.loader
lmm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lmm
_spec.loader.exec_module(lmm)


def _architecture(tmp_path: Path, rows: str) -> Path:
    """An architecture page holding just the module table, plus a section after it."""
    path = tmp_path / "architecture.md"
    path.write_text(
        "# Architecture\n\n## Module list and roles\n\n"
        "| Module | Role | Page |\n|---|---|---|\n"
        f"{rows}\n"
        "## Dependencies (layers)\n\n`never_counted.py` is prose, not a table row.\n",
        encoding="utf-8",
    )
    return path


def _package(tmp_path: Path, *names: str) -> Path:
    """A package tree: a name ending in / becomes a subpackage, otherwise a module."""
    pkg = tmp_path / "bajutsu"
    (pkg).mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in names:
        if name.endswith("/"):
            (pkg / name).mkdir(parents=True, exist_ok=True)
            (pkg / name / "__init__.py").write_text("", encoding="utf-8")
        else:
            (pkg / name).write_text("", encoding="utf-8")
    return pkg


def test_table_names_reads_every_name_in_a_cell(tmp_path: Path) -> None:
    """One cell may document several modules, separated by a middle dot."""
    page = _architecture(
        tmp_path, "| `analysis/` · `serve/flakiness.py` | Advisory analysis | — |\n"
    )

    assert lmm.table_names(page) == {"analysis/", "serve/flakiness.py"}


def test_table_names_stops_at_the_next_section(tmp_path: Path) -> None:
    """Prose after the table is not a row, so a name there must not count as documented."""
    page = _architecture(tmp_path, "| `drivers/base.py` | The determinism core | — |\n")

    assert "never_counted.py" not in lmm.table_names(page)


def test_table_names_rejects_a_page_without_the_section(tmp_path: Path) -> None:
    """A renamed heading would make every rule pass against an empty table, so it fails loudly."""
    page = tmp_path / "architecture.md"
    page.write_text("# Architecture\n\n## Something else\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Module list and roles"):
        lmm.table_names(page)


def test_missing_from_tree_finds_a_row_pointing_at_nothing(tmp_path: Path) -> None:
    """A row naming a renamed or deleted module sends a reader somewhere empty."""
    pkg = _package(tmp_path, "real.py")

    assert lmm.missing_from_tree({"real.py", "ghost.py"}, pkg) == ["ghost.py"]


def test_undocumented_packages_accepts_a_file_inside_the_package(tmp_path: Path) -> None:
    """`drivers/` has no row of its own; its files document it, so it is not a gap."""
    pkg = _package(tmp_path, "drivers/")
    (pkg / "drivers" / "base.py").write_text("", encoding="utf-8")

    assert lmm.undocumented_packages({"drivers/base.py"}, pkg) == []


def test_undocumented_packages_reports_a_package_nothing_mentions(tmp_path: Path) -> None:
    """A new subpackage the table never names is exactly the gap this rule exists to catch."""
    pkg = _package(tmp_path, "drivers/", "brandnew/")
    (pkg / "brandnew" / "thing.py").write_text("", encoding="utf-8")

    assert lmm.undocumented_packages({"drivers/"}, pkg) == ["brandnew/"]


def test_undocumented_packages_skips_a_directory_without_modules(tmp_path: Path) -> None:
    """A templates directory holds no Python, so the table owes it no row."""
    pkg = _package(tmp_path)
    (pkg / "templates").mkdir()

    assert lmm.undocumented_packages(set(), pkg) == []


def test_undocumented_modules_honours_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grandfathered module stays quiet; a new one fails until its row lands."""
    pkg = _package(tmp_path, "old.py", "brand_new.py", "documented.py")
    monkeypatch.setattr(lmm, "GRANDFATHERED", frozenset({"old.py"}))

    assert lmm.undocumented_modules({"documented.py"}, pkg) == ["brand_new.py"]


def test_undocumented_modules_skips_dunder_modules(tmp_path: Path) -> None:
    """`__main__.py` is an entry-point convention, not a feature the map should describe."""
    pkg = _package(tmp_path, "__main__.py")

    assert lmm.undocumented_modules(set(), pkg) == []


def test_stale_grandfathered_reports_a_vanished_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist must not outlive the modules it excuses."""
    pkg = _package(tmp_path, "still_here.py")
    monkeypatch.setattr(lmm, "GRANDFATHERED", frozenset({"still_here.py", "long_gone.py"}))

    assert lmm.stale_grandfathered(pkg) == ["long_gone.py"]


def test_stale_grandfathered_reports_an_entry_the_table_now_documents(
    tmp_path: Path, monkeypatch
) -> None:
    """An entry that earned a row must leave the list, or rule 3 stops protecting that module."""
    pkg = _package(tmp_path, "documented.py", "still_missing.py")
    monkeypatch.setattr(lmm, "GRANDFATHERED", frozenset({"documented.py", "still_missing.py"}))

    assert lmm.stale_grandfathered(pkg, {"documented.py"}) == ["documented.py"]


def test_main_passes_on_a_matching_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A table that covers the tree exits zero and says how much it checked."""
    monkeypatch.setattr(lmm, "GRANDFATHERED", frozenset())
    pkg = _package(tmp_path, "drivers/", "doctor.py")
    (pkg / "drivers" / "base.py").write_text("", encoding="utf-8")
    page = _architecture(
        tmp_path, "| `drivers/base.py` | Core | — |\n| `doctor.py` | Score | — |\n"
    )

    code = lmm.main(["--architecture", str(page), "--package", str(pkg)])

    assert code == 0
    assert "2 table entries" in capsys.readouterr().out


def test_main_fails_and_names_every_offender(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure names each gap and the remedy, so the message is actionable on its own."""
    monkeypatch.setattr(lmm, "GRANDFATHERED", frozenset())
    pkg = _package(tmp_path, "undocumented.py")
    page = _architecture(tmp_path, "| `ghost.py` | Gone | — |\n")

    code = lmm.main(["--architecture", str(page), "--package", str(pkg)])

    assert code != 0
    err = capsys.readouterr().err
    assert "ghost.py" in err
    assert "undocumented.py" in err
    assert "2 problem(s)" in err


def test_main_reports_a_missing_package_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad --package path fails with a message rather than an unhandled exception."""
    page = _architecture(tmp_path, "| `doctor.py` | Score | — |\n")

    code = lmm.main(["--architecture", str(page), "--package", str(tmp_path / "nope")])

    assert code != 0
    assert "not a directory" in capsys.readouterr().err


def test_the_real_tree_passes() -> None:
    """The check is green on the repository as it stands, so the gate starts from a clean base."""
    repo = Path(__file__).resolve().parent.parent

    assert (
        lmm.main(
            ["--architecture", str(repo / lmm.ARCHITECTURE), "--package", str(repo / lmm.PACKAGE)]
        )
        == 0
    )

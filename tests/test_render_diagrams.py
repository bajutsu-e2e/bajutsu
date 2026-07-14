"""Tests for scripts/render_diagrams.py — mermaid-fence marker extraction (BE-XXXX).

Pins the pure pieces (``_MARKER_RE``, ``_find_markdown_files``) that don't need Node or
mermaid-cli — ``_render_one`` shells out to ``npx @mermaid-js/mermaid-cli`` and is exercised
manually via ``make docs-diagrams`` instead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "render_diagrams.py"
_spec = importlib.util.spec_from_file_location("render_diagrams", _MODULE_PATH)
assert _spec and _spec.loader
rd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rd
_spec.loader.exec_module(rd)


def test_marker_re_extracts_path_and_source() -> None:
    text = (
        "intro text\n\n"
        "<!-- mermaid-svg: assets/diagrams/example.svg -->\n"
        "```mermaid\n"
        "flowchart TB\n"
        "    a --> b\n"
        "```\n\n"
        "trailing text\n"
    )
    matches = list(rd._MARKER_RE.finditer(text))
    assert len(matches) == 1
    assert matches[0]["path"] == "assets/diagrams/example.svg"
    assert matches[0]["source"] == "flowchart TB\n    a --> b"


def test_marker_re_finds_multiple_diagrams_in_one_file() -> None:
    text = (
        "<!-- mermaid-svg: a.svg -->\n```mermaid\nflowchart TB\n    x --> y\n```\n\n"
        "<!-- mermaid-svg: b.svg -->\n```mermaid\nflowchart TB\n    p --> q\n```\n"
    )
    matches = list(rd._MARKER_RE.finditer(text))
    assert [m["path"] for m in matches] == ["a.svg", "b.svg"]


def test_marker_re_ignores_plain_mermaid_fence_without_marker() -> None:
    text = "```mermaid\nflowchart TB\n    a --> b\n```\n"
    assert list(rd._MARKER_RE.finditer(text)) == []


def test_find_markdown_files_defaults_to_docs_dir() -> None:
    files = rd._find_markdown_files([])
    assert files == sorted(files)
    assert all(p.suffix == ".md" for p in files)
    assert all(rd.DOCS_DIR in p.parents for p in files)


def test_find_markdown_files_uses_explicit_list(tmp_path: Path) -> None:
    md = tmp_path / "sample.md"
    md.write_text("# hi\n", encoding="utf-8")
    files = rd._find_markdown_files([str(md)])
    assert files == [md.resolve()]

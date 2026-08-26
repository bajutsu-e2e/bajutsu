"""Tests for the roadmap filter query (scripts/roadmap_query.py, BE-0162).

The query projects every BE item's own metadata into a small table filtered by ``Status``,
keyword, ``Topic``, or id, so an AI session can survey just the rows its question needs without
reading the whole index. These tests build a temporary roadmap tree and pin the pure pieces —
status and id resolution, each filter over the tree, and table rendering — plus the CLI's exit
codes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "roadmap_query.py"
_spec = importlib.util.spec_from_file_location("roadmap_query", _MODULE_PATH)
assert _spec and _spec.loader
rq = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rq
_spec.loader.exec_module(rq)


def _item(status: str, topic: str, title: str, slug: str, id_: str = "BE-0001") -> str:
    """A minimal English BE item file body carrying the metadata the query reads."""
    return (
        f"**English** · [日本語]({id_}-{slug}-ja.md)\n\n"
        f"# {id_} — {title}\n\n"
        "<!-- BE-METADATA -->\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Proposal | [{id_}]({id_}-{slug}.md) |\n"
        f"| Status | **{status}** |\n"
        f"| Topic | {topic} |\n"
        "<!-- /BE-METADATA -->\n\n"
        "## Introduction\n"
    )


def _item_with_intro(status: str, topic: str, title: str, slug: str, id_: str, intro: str) -> str:
    """The same minimal item, with a first Introduction paragraph for --grep to search."""
    return _item(status, topic, title, slug, id_) + f"\n{intro}\n"


def _write_item(roadmap: Path, id_: str, slug: str, body: str) -> None:
    d = roadmap / f"{id_}-{slug}"
    d.mkdir(parents=True)
    (d / f"{id_}-{slug}.md").write_text(body, encoding="utf-8")


def test_resolve_status_is_case_insensitive() -> None:
    """A status is matched to its canonical form regardless of case."""
    assert rq.resolve_status("proposal") == "Proposal"
    assert rq.resolve_status("IN PROGRESS") == "In progress"
    assert rq.resolve_status("DEFERRED") == "Deferred"
    assert rq.resolve_status("rejected") == "Rejected"


def test_resolve_status_rejects_unknown() -> None:
    """An unknown status fails with the valid values named, rather than matching nothing."""
    with pytest.raises(ValueError, match=r"In progress.*Proposal"):
        rq.resolve_status("done")


def test_iter_rows_returns_only_matching_status(tmp_path: Path) -> None:
    """Filtering by a status returns exactly the items whose metadata carries it."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0001", "alpha", _item("Proposal", "MCP", "Alpha", "alpha", "BE-0001"))
    _write_item(roadmap, "BE-0002", "beta", _item("Implemented", "MCP", "Beta", "beta", "BE-0002"))
    _write_item(
        roadmap, "BE-0003", "gamma", _item("Proposal", "doctor", "Gamma", "gamma", "BE-0003")
    )

    rows = rq.iter_rows(roadmap, "Proposal")

    assert [row.id for row in rows] == ["BE-0001", "BE-0003"]
    assert [row.title for row in rows] == ["Alpha", "Gamma"]


def test_iter_rows_sorts_by_topic_then_id(tmp_path: Path) -> None:
    """Rows are ordered by Topic first, then ID, for stable output."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0005", "e", _item("Proposal", "doctor", "E", "e", "BE-0005"))
    _write_item(roadmap, "BE-0004", "d", _item("Proposal", "MCP", "D", "d", "BE-0004"))
    _write_item(roadmap, "BE-0006", "f", _item("Proposal", "MCP", "F", "f", "BE-0006"))

    rows = rq.iter_rows(roadmap, "Proposal")

    assert [(row.topic, row.id) for row in rows] == [
        ("MCP", "BE-0004"),
        ("MCP", "BE-0006"),
        ("doctor", "BE-0005"),
    ]


def test_iter_rows_carries_relative_path(tmp_path: Path) -> None:
    """Each row's Path is the relative path to the item's English .md — what to Read next."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0007", "gizmo", _item("Proposal", "MCP", "Gizmo", "gizmo", "BE-0007"))

    (row,) = rq.iter_rows(roadmap, "Proposal")

    assert row.path == "roadmaps/BE-0007-gizmo/BE-0007-gizmo.md"


def test_iter_rows_includes_placeholder_item(tmp_path: Path) -> None:
    """An in-flight placeholder (BE-XXXX) is read for its Status like any numbered item."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-XXXX", "draft", _item("Proposal", "MCP", "Draft", "draft", "BE-XXXX"))

    (row,) = rq.iter_rows(roadmap, "Proposal")

    assert row.id == "BE-XXXX"
    assert row.title == "Draft"


def test_iter_rows_rejects_malformed_heading_id(tmp_path: Path) -> None:
    """A matching item with a malformed id heading fails loudly, naming the offending file."""
    roadmap = tmp_path / "roadmaps"
    body = _item("Proposal", "MCP", "Bad", "bad", "BE-0013").replace("# BE-0013 —", "# BE-13 —")
    _write_item(roadmap, "BE-0013", "bad", body)

    with pytest.raises(ValueError, match=r"BE-0013-bad\.md.*heading"):
        rq.iter_rows(roadmap, "Proposal")


def test_iter_rows_rejects_matching_item_missing_topic(tmp_path: Path) -> None:
    """A status-matched item without a Topic field fails with the offending file named."""
    roadmap = tmp_path / "roadmaps"
    body = _item("Proposal", "MCP", "NoTopic", "notopic", "BE-0014").replace(
        "| Topic | MCP |\n", ""
    )
    _write_item(roadmap, "BE-0014", "notopic", body)

    with pytest.raises(ValueError, match=r"BE-0014-notopic\.md.*Topic"):
        rq.iter_rows(roadmap, "Proposal")


def test_iter_rows_rejects_unknown_status(tmp_path: Path) -> None:
    """The filter validates its status argument before scanning."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0008", "h", _item("Proposal", "MCP", "H", "h", "BE-0008"))

    with pytest.raises(ValueError, match="Proposal"):
        rq.iter_rows(roadmap, "nonsense")


def test_render_table_shape(tmp_path: Path) -> None:
    """The rendered table has the ID / Item / Topic / Path header and one row per item."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0009", "i", _item("Proposal", "MCP", "Widget", "i", "BE-0009"))

    table = rq.render_table(rq.iter_rows(roadmap, "Proposal"))
    lines = table.splitlines()

    assert lines[0] == "| ID | Item | Topic | Path |"
    assert lines[1] == "|---|---|---|---|"
    assert lines[2] == ("| BE-0009 | Widget | MCP | roadmaps/BE-0009-i/BE-0009-i.md |")


def test_main_unknown_status_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown status on the CLI prints the valid values and exits non-zero."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0010", "j", _item("Proposal", "MCP", "J", "j", "BE-0010"))

    code = rq.main(["--status", "bogus", "--roadmap", str(roadmap)])

    assert code != 0
    assert "Proposal" in capsys.readouterr().err


def test_main_valid_status_prints_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A valid status prints the filtered table and exits zero."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0011", "k", _item("Proposal", "MCP", "Kappa", "k", "BE-0011"))
    _write_item(roadmap, "BE-0012", "l", _item("Implemented", "MCP", "Lambda", "l", "BE-0012"))

    code = rq.main(["--status", "proposal", "--roadmap", str(roadmap)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Kappa" in out
    assert "Lambda" not in out


def test_resolve_item_id_accepts_the_usual_forms() -> None:
    """An id is accepted however a branch name or a PR title happened to write it."""
    assert rq.resolve_item_id("BE-0349") == "BE-0349"
    assert rq.resolve_item_id("be-0349") == "BE-0349"
    assert rq.resolve_item_id("0349") == "BE-0349"
    assert rq.resolve_item_id("349") == "BE-0349"
    assert rq.resolve_item_id("BE-XXXX") == "BE-XXXX"
    assert rq.resolve_item_id("xxxx") == "BE-XXXX"


def test_resolve_item_id_rejects_nonsense() -> None:
    """An unparseable id fails before any file is read, naming the shapes it accepts."""
    with pytest.raises(ValueError, match=r"BE-NNNN"):
        rq.resolve_item_id("scroll")


def test_grep_matches_the_title(tmp_path: Path) -> None:
    """A keyword search finds an item whose title carries the word."""
    roadmap = tmp_path / "roadmaps"
    _write_item(
        roadmap, "BE-0020", "a", _item("Proposal", "MCP", "Scroll to element", "a", "BE-0020")
    )
    _write_item(roadmap, "BE-0021", "b", _item("Proposal", "MCP", "Tap fidelity", "b", "BE-0021"))

    rows = rq.iter_rows(roadmap, grep="scroll")

    assert [row.id for row in rows] == ["BE-0020"]


def test_grep_matches_the_introduction_excerpt(tmp_path: Path) -> None:
    """A keyword search reaches the Introduction excerpt, not the title alone."""
    roadmap = tmp_path / "roadmaps"
    body = _item_with_intro(
        "Proposal", "MCP", "Bounded safety net", "c", "BE-0022", "The step scrolls until it lands."
    )
    _write_item(roadmap, "BE-0022", "c", body)

    assert [row.id for row in rq.iter_rows(roadmap, grep="scrolls")] == ["BE-0022"]
    assert rq.iter_rows(roadmap, grep="teleports") == []


def test_grep_is_case_insensitive(tmp_path: Path) -> None:
    """A keyword matches regardless of the case either side was written in."""
    roadmap = tmp_path / "roadmaps"
    _write_item(
        roadmap, "BE-0023", "d", _item("Proposal", "MCP", "Scroll Fidelity", "d", "BE-0023")
    )

    assert [row.id for row in rq.iter_rows(roadmap, grep="SCROLL")] == ["BE-0023"]


def test_topic_filter_matches_a_substring(tmp_path: Path) -> None:
    """A topic filter matches part of the Topic, case-insensitively."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0024", "e", _item("Proposal", "Driver & backend", "E", "e", "BE-0024"))
    _write_item(roadmap, "BE-0025", "f", _item("Proposal", "Platform support", "F", "f", "BE-0025"))

    assert [row.id for row in rq.iter_rows(roadmap, topic="driver")] == ["BE-0024"]


def test_id_filter_selects_one_item(tmp_path: Path) -> None:
    """An id filter returns that item alone, whatever its status."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0026", "g", _item("Implemented", "MCP", "G", "g", "BE-0026"))
    _write_item(roadmap, "BE-0027", "h", _item("Proposal", "MCP", "H", "h", "BE-0027"))

    rows = rq.iter_rows(roadmap, item_id="26")

    assert [(row.id, row.status) for row in rows] == [("BE-0026", "Implemented")]


def test_filters_compose(tmp_path: Path) -> None:
    """Two filters return the intersection, not the union."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0028", "i", _item("Proposal", "Driver", "Scroll", "i", "BE-0028"))
    _write_item(roadmap, "BE-0029", "j", _item("Implemented", "Driver", "Scroll", "j", "BE-0029"))
    _write_item(roadmap, "BE-0030", "k", _item("Proposal", "Serve", "Scroll", "k", "BE-0030"))

    rows = rq.iter_rows(roadmap, "Proposal", topic="driver", grep="scroll")

    assert [row.id for row in rows] == ["BE-0028"]


def test_iter_rows_without_a_filter_returns_every_item(tmp_path: Path) -> None:
    """The function itself does not require a filter; only the CLI refuses an unfiltered dump."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0031", "m", _item("Proposal", "MCP", "M", "m", "BE-0031"))
    _write_item(roadmap, "BE-0032", "n", _item("Rejected", "MCP", "N", "n", "BE-0032"))

    assert [row.id for row in rq.iter_rows(roadmap)] == ["BE-0031", "BE-0032"]


def test_render_table_with_status_adds_a_column(tmp_path: Path) -> None:
    """A search that can span statuses renders a Status column; the four-column shape is intact."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0033", "o", _item("Deferred", "MCP", "Widget", "o", "BE-0033"))

    table = rq.render_table(rq.iter_rows(roadmap, grep="widget"), with_status=True)
    lines = table.splitlines()

    assert lines[0] == "| ID | Item | Status | Topic | Path |"
    assert lines[1] == "|---|---|---|---|---|"
    assert lines[2] == "| BE-0033 | Widget | Deferred | MCP | roadmaps/BE-0033-o/BE-0033-o.md |"


def test_main_without_a_filter_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unfiltered CLI call names the filters instead of dumping every item."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0034", "p", _item("Proposal", "MCP", "P", "p", "BE-0034"))

    code = rq.main(["--roadmap", str(roadmap)])

    assert code != 0
    err = capsys.readouterr().err
    assert "--grep" in err and "--topic" in err and "--id" in err


def test_main_status_query_keeps_the_four_column_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A --status query renders the original header, so the roadmap-filter skill's shape holds."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0035", "q", _item("Proposal", "MCP", "Q", "q", "BE-0035"))

    assert rq.main(["--status", "Proposal", "--roadmap", str(roadmap)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "| ID | Item | Topic | Path |"


def test_main_grep_query_shows_the_status_column(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A keyword query can span statuses, so its table names each row's own Status."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0036", "r", _item("Deferred", "MCP", "Rabbit", "r", "BE-0036"))

    assert rq.main(["--grep", "rabbit", "--roadmap", str(roadmap)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "| ID | Item | Status | Topic | Path |"
    assert "Deferred" in out[2]


def test_main_bad_id_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unparseable --id fails on the CLI with the accepted shapes named."""
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "BE-0037", "s", _item("Proposal", "MCP", "S", "s", "BE-0037"))

    code = rq.main(["--id", "banana", "--roadmap", str(roadmap)])

    assert code != 0
    assert "BE-NNNN" in capsys.readouterr().err

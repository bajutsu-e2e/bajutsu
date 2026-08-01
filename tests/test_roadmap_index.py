"""Tests for the roadmap metadata loader (scripts/build_roadmap_index.py).

The loader reads each BE item's own metadata into a plain in-memory model — consumed by the roadmap
dashboard generator and a handful of other roadmap tools. These tests pin the pure pieces — metadata
parsing, status-to-bucket classification, and duplicate-id detection — plus loading the real,
committed roadmap tree end to end.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_roadmap_index.py"
_spec = importlib.util.spec_from_file_location("build_roadmap_index", _MODULE_PATH)
assert _spec and _spec.loader
bri = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bri  # let dataclass resolve annotations during exec
_spec.loader.exec_module(bri)


EN_FILE = """\
**English** · [日本語](BE-0029-visual-regression-assertions-ja.md)

# BE-0029 — Visual-regression assertions

* Proposal: [BE-0029](BE-0029-visual-regression-assertions.md)
* Status: **Implemented**
* Topic: Verification & coverage
* Origin: Both

## Introduction
"""

JA_FILE = """\
[English](BE-0029-visual-regression-assertions.md) · **日本語**

# BE-0029 — ビジュアル回帰アサーション

* 提案: [BE-0029](BE-0029-visual-regression-assertions-ja.md)
* 状態: **実装済み**
* トピック: 検証とカバレッジ
* 由来: 両社

## はじめに
"""


FENCED_EN_FILE = """\
**English** · [日本語](BE-0029-visual-regression-assertions-ja.md)

# BE-0029 — Visual-regression assertions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0029](BE-0029-visual-regression-assertions.md) |
| Status | **Implemented** |
| Topic | Verification & coverage |
| Origin | Both |
<!-- /BE-METADATA -->

## Detailed design

A same-shaped table in the body must not be read as metadata:

| Field | Value |
|---|---|
| Status | not-metadata |
"""


def test_parse_metadata_reads_fenced_table() -> None:
    title, fields = bri.parse_metadata(FENCED_EN_FILE)
    assert title == "Visual-regression assertions"
    # The fenced data rows are read; the header row and the body table are not.
    assert fields["Status"] == "Implemented"
    assert fields["Topic"] == "Verification & coverage"
    assert fields["Origin"] == "Both"
    assert "Field" not in fields


def test_parse_metadata_reads_title_and_fields() -> None:
    title, fields = bri.parse_metadata(EN_FILE)
    assert title == "Visual-regression assertions"
    assert fields["Status"] == "Implemented"
    assert fields["Topic"] == "Verification & coverage"
    assert fields["Origin"] == "Both"


def test_parse_metadata_japanese_fields() -> None:
    title, fields = bri.parse_metadata(JA_FILE)
    assert title == "ビジュアル回帰アサーション"
    assert fields["状態"] == "実装済み"
    assert fields["由来"] == "両社"


def test_bucket_derives_classification_from_status() -> None:
    # Status is the lone lifecycle field; the bucket is derived from it, not a hand-set Track
    # (retired in BE-0078) — so folder and bucket can never disagree.
    assert bri.bucket("Implemented") == "Implemented"
    assert bri.bucket("In progress") == "In progress"
    assert bri.bucket("Proposal") == "Proposals"
    assert bri.bucket("Proposal (deferred)") == "Deferred"


def test_bucket_rejects_unknown_status() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown status"):
        bri.bucket("Something else")


def test_tracking_issue_url_is_a_pure_function_of_the_id() -> None:
    url = bri.tracking_issue_url("BE-0139")
    assert url.startswith("https://github.com/bajutsu-e2e/bajutsu/issues")
    assert "roadmap-tracking" in url
    assert "BE-0139" in url


def test_duplicate_ids_flags_collisions(tmp_path: Path) -> None:
    """duplicate_ids reports any BE number shared by two item directories."""
    roadmap = tmp_path / "roadmaps"
    for name in ("BE-0045-foo", "BE-0045-bar", "BE-0046-baz"):
        (roadmap / name).mkdir(parents=True)
    dupes = bri.duplicate_ids(roadmap)
    assert set(dupes) == {"BE-0045"}
    assert sorted(dupes["BE-0045"]) == ["BE-0045-bar", "BE-0045-foo"]


def test_load_items_rejects_duplicate_ids(tmp_path: Path) -> None:
    """The loader refuses a tree with a duplicate id instead of loading two items for it."""
    import pytest

    roadmap = tmp_path / "roadmaps"
    for name in ("BE-0045-foo", "BE-0045-bar"):
        (roadmap / name).mkdir(parents=True)
    with pytest.raises(ValueError, match="duplicate BE IDs"):
        bri.load_items(roadmap)


def test_no_duplicate_be_ids() -> None:
    """The gate: no two roadmap items share a BE id (IDs are unique and permanent)."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    assert bri.duplicate_ids(roadmap) == {}, "duplicate BE IDs found in roadmaps/"


def test_load_items_loads_the_committed_roadmap_tree() -> None:
    """The gate: every committed item parses, and its Topic is one of the known topics (BE-0074)."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    items = bri.load_items(roadmap)
    assert items, "expected at least one roadmap item"
    for item in items:
        assert item.bucket in dict(bri.BUCKETS)
        assert item.topic in bri.KNOWN_TOPICS
        assert "en" in item.by_lang and "ja" in item.by_lang
        # Every committed item's English file opens with an "## Introduction" section, so every
        # loaded item carries a non-empty summary.
        assert item.summary, f"{item.id} loaded with no summary"


class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def test_git_dates_combines_both_files_and_normalises_to_utc(monkeypatch: Any) -> None:
    """created/updated are the oldest/newest commit across both files, every stamp in UTC (BE-0311).

    A ``+09:00`` stamp must be compared as its UTC instant, not lexically — 19:22:34+09:00 is
    10:22:34Z, earlier than a 12:00Z stamp the same day — so the min/max come out chronological.
    """
    per_file = {
        "en.md": "2026-07-17T19:22:34+09:00\n2026-07-10T00:00:00+00:00\n",
        "ja.md": "2026-07-20T12:00:00+00:00\n2026-07-12T00:00:00+00:00\n",
    }

    def fake_run(cmd: list[str], **_kw: Any) -> _FakeProc:
        path = cmd[-1]
        for suffix, out in per_file.items():
            if path.endswith(suffix):
                return _FakeProc(out)
        return _FakeProc("")

    monkeypatch.setattr(bri.subprocess, "run", fake_run)
    created, updated = bri._git_dates([Path("a/en.md"), Path("a/ja.md")])
    assert created == "2026-07-10T00:00:00+00:00"
    assert updated == "2026-07-20T12:00:00+00:00"


def test_git_dates_normalises_a_non_utc_offset(monkeypatch: Any) -> None:
    """A lone non-UTC stamp is returned as its UTC instant, so the dashboard sort stays correct."""
    monkeypatch.setattr(
        bri.subprocess, "run", lambda *a, **k: _FakeProc("2026-07-17T19:22:34+09:00\n")
    )
    created, updated = bri._git_dates([Path("x.md")])
    assert created == updated == "2026-07-17T10:22:34+00:00"


def test_git_dates_returns_none_when_history_is_empty(monkeypatch: Any) -> None:
    """No commits (a shallow clone, an uncommitted file) yields no invented date."""
    monkeypatch.setattr(bri.subprocess, "run", lambda *a, **k: _FakeProc(""))
    assert bri._git_dates([Path("x.md")]) == (None, None)


def test_git_dates_survives_missing_git(monkeypatch: Any) -> None:
    """No ``git`` on PATH is tolerated: the dashboard renders dateless rather than crashing."""

    def boom(*_a: Any, **_k: Any) -> _FakeProc:
        raise FileNotFoundError

    monkeypatch.setattr(bri.subprocess, "run", boom)
    assert bri._git_dates([Path("x.md")]) == (None, None)


def test_load_items_with_dates_are_opt_in() -> None:
    """with_dates fills created/updated as aware UTC ISO (or None); the default leaves them None."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    for item in bri.load_items(roadmap, with_dates=True):
        for stamp in (item.created, item.updated):
            if stamp is not None:
                assert datetime.fromisoformat(stamp).tzinfo is not None
    # Off by default, so the tools that don't render dates skip the per-item git log calls.
    assert all(i.created is None and i.updated is None for i in bri.load_items(roadmap))


def test_relation_ids_reads_every_id_a_field_names() -> None:
    """A relation value's ids are read by token, in first-seen order, deduped, minus the item."""
    value = (
        "[BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md), "
        "[BE-0219](../BE-0219-roadmap-dashboard-search/BE-0219-roadmap-dashboard-search.md)"
    )
    assert bri.relation_ids(value, self_id="BE-0311") == ("BE-0094", "BE-0219")
    # An id repeated by a link's text and its href is one reference, not two.
    assert bri.relation_ids("BE-0094 [BE-0094](x.md)", self_id="BE-0311") == ("BE-0094",)
    # Self-references and an unset field yield nothing rather than a self-loop or a crash.
    assert bri.relation_ids("[BE-0311](BE-0311-x.md)", self_id="BE-0311") == ()
    assert bri.relation_ids(None, self_id="BE-0311") == ()
    # A BE-XXXX placeholder carries no number, so it names no item to link to.
    assert bri.relation_ids("BE-XXXX", self_id="BE-0311") == ()


def test_extract_summary_reads_the_first_introduction_paragraph() -> None:
    """A link's visible text survives, other markup is stripped, and wrapped lines join with spaces."""
    text = (
        "# BE-0029 — Title\n\n## Introduction\n\n"
        "Add a [thing](x.md) that **matters**, spanning\n"
        "two wrapped lines.\n\n## Motivation\n\nignored\n"
    )
    assert bri.extract_summary(text) == "Add a thing that matters, spanning two wrapped lines."


def test_extract_summary_preserves_underscores_inside_inline_code() -> None:
    """A code span's identifiers must survive verbatim, not lose their underscores to emphasis-stripping.

    Regression test: an inline code span like ``` `Driver.wait_for` ``` or ``` `__init__` ``` looks,
    once its backticks are gone, exactly like underscore-delimited emphasis around ``wait_for`` /
    ``init`` — the two are only distinguishable while the backticks are still there, so the fix must
    strip a code span's backticks without ever running the emphasis rule over its contents.
    """
    text = (
        "## Introduction\n\n"
        "Call `Driver.wait_for()` and `__init__` before `_header_names` runs.\n\n## Motivation\n"
    )
    assert (
        bri.extract_summary(text)
        == "Call Driver.wait_for() and __init__ before _header_names runs."
    )


def test_extract_summary_leaves_a_bare_snake_case_identifier_untouched() -> None:
    """A single underscore between word characters is never a valid emphasis delimiter (intraword)."""
    text = "## Introduction\n\nwait_for and set_value both changed.\n\n## Motivation\n"
    assert bri.extract_summary(text) == "wait_for and set_value both changed."


def test_extract_summary_still_strips_genuine_underscore_italics() -> None:
    """Real emphasis — delimiters flanked by non-word characters — is still stripped as before."""
    text = "## Introduction\n\nSee _the docs_ for more.\n\n## Motivation\n"
    assert bri.extract_summary(text) == "See the docs for more."


def test_extract_summary_protects_code_spans_before_link_resolution() -> None:
    """A code span is stashed *before* link stripping runs, so link-shaped text inside it survives.

    Regression test: link stripping used to run first, so literal text that happens to look like a
    markdown link — e.g. an Introduction quoting the ``Related`` field's own syntax — would already
    be resolved away by the time the code span around it was noticed, corrupting content that was
    never meant to be treated as a real link.
    """
    text = "## Introduction\n\nExample: `[BE-0014](../x.md)` is the syntax.\n\n## Motivation\n"
    assert bri.extract_summary(text) == "Example: [BE-0014](../x.md) is the syntax."
    # A real link whose visible text itself contains a code span still resolves as before.
    text2 = "## Introduction\n\nSee [`Driver.wait`](url) for details.\n\n## Motivation\n"
    assert bri.extract_summary(text2) == "See Driver.wait for details."


def test_extract_summary_truncates_a_long_paragraph_at_a_word_boundary() -> None:
    """A paragraph past ``max_len`` is cut at the last whole word, not mid-word, and marked with …"""
    text = "## Introduction\n\n" + ("word " * 100).strip() + "\n\n## Motivation\n"
    summary = bri.extract_summary(text, max_len=20)
    assert summary == "word word word word…"
    assert len(summary) <= 21


def test_extract_summary_is_empty_without_an_introduction_section() -> None:
    """A file with no ``## Introduction`` (the legacy-format fixtures above) yields no summary."""
    assert bri.extract_summary(EN_FILE) == ""
    assert bri.extract_summary("# BE-0029 — Title\n\nNo introduction heading here.\n") == ""


def _item_dir(roadmap: Path, be_id: str, slug: str, *, extra: str = "") -> None:
    """Write a minimal two-language item under ``roadmap``, with optional extra metadata rows."""
    directory = roadmap / f"{be_id}-{slug}"
    directory.mkdir(parents=True)
    for suffix, status, topic in (("", "Status", "Topic"), ("-ja", "状態", "トピック")):
        (directory / f"{be_id}-{slug}{suffix}.md").write_text(
            f"# {be_id} — Title\n\n"
            "<!-- BE-METADATA -->\n| Field | Value |\n|---|---|\n"
            f"| {status} | **Implemented** |\n| {topic} | Contributor workflow |\n"
            f"{extra if not suffix else ''}"
            "<!-- /BE-METADATA -->\n",
            encoding="utf-8",
        )


def test_load_items_drops_a_relation_to_an_item_that_does_not_exist(tmp_path: Path) -> None:
    """A relation is kept only when the tree holds the item it names, so no edge is left dangling."""
    roadmap = tmp_path / "roadmaps"
    _item_dir(
        roadmap,
        "BE-0045",
        "foo",
        extra="| Related | [BE-0046](x.md), [BE-9999](y.md) |\n| Superseded by | [BE-0046](z.md) |\n",
    )
    _item_dir(roadmap, "BE-0046", "bar")
    items = {item.id: item for item in bri.load_items(roadmap)}
    assert items["BE-0045"].related == ("BE-0046",)
    assert items["BE-0045"].superseded_by == ("BE-0046",)
    assert items["BE-0046"].related == ()


def test_load_items_reads_the_summary_from_the_english_introduction_only(tmp_path: Path) -> None:
    """summary comes off the English file's Introduction, like Status and Topic — Japanese never adds one."""
    roadmap = tmp_path / "roadmaps"
    _item_dir(roadmap, "BE-0045", "foo")
    en = roadmap / "BE-0045-foo" / "BE-0045-foo.md"
    en.write_text(
        en.read_text(encoding="utf-8") + "\n## Introduction\n\nWhat this item does.\n",
        encoding="utf-8",
    )
    ja = roadmap / "BE-0045-foo" / "BE-0045-foo-ja.md"
    ja.write_text(
        ja.read_text(encoding="utf-8") + "\n## Introduction\n\nJapanese text.\n", encoding="utf-8"
    )
    items = {item.id: item for item in bri.load_items(roadmap)}
    assert items["BE-0045"].summary == "What this item does."


def test_load_items_reads_relations_from_the_english_file_only(tmp_path: Path) -> None:
    """Relations come off the English metadata, like Status and Topic — Japanese never adds one."""
    roadmap = tmp_path / "roadmaps"
    _item_dir(roadmap, "BE-0045", "foo", extra="| Origin | [BE-0046](x.md) |\n")
    _item_dir(roadmap, "BE-0046", "bar")
    ja = roadmap / "BE-0046-bar" / "BE-0046-bar-ja.md"
    ja.write_text(
        ja.read_text(encoding="utf-8").replace(
            "<!-- /BE-METADATA -->", "| 関連 | [BE-0045](x.md) |\n<!-- /BE-METADATA -->"
        ),
        encoding="utf-8",
    )
    items = {item.id: item for item in bri.load_items(roadmap)}
    assert items["BE-0045"].origin_refs == ("BE-0046",)
    assert items["BE-0046"].related == ()


def test_committed_relations_all_resolve() -> None:
    """The gate: every relation the committed roadmap declares names an item that exists."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    items = bri.load_items(roadmap)
    known = {item.id for item in items}
    for item in items:
        for attr in ("related", "origin_refs", "superseded_by"):
            refs = getattr(item, attr)
            assert set(refs) <= known, f"{item.id}.{attr}"
            assert item.id not in refs, f"{item.id}.{attr} points at itself"

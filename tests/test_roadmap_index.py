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

"""Tests for scripts/sync_roadmap_tracking_issues.py (BE-0109).

The sync keeps one open GitHub ``roadmap-tracking`` issue per *open* roadmap item (Status
``Proposal`` / ``In progress``) and closes an item's issue once it ships or is shelved. GitHub is
the source of truth, so the lifecycle decision (``plan``) is a pure function of the tree's Statuses
plus the ids that currently have an open issue — that purity is what these tests pin. The ``gh``
side effects are exercised through monkeypatched seams, never the network.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "sync_roadmap_tracking_issues.py"
)
_spec = importlib.util.spec_from_file_location("sync_roadmap_tracking_issues", _MODULE_PATH)
assert _spec and _spec.loader
sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync
_spec.loader.exec_module(sync)


def _write_item(
    roadmap: Path,
    category: str,
    name: str,
    status: str,
    *,
    title: str = "demo item",
    intro: str = "The one-line pitch.",
) -> Path:
    """Create a minimal BE item directory (both language files) with the given fields."""
    item = roadmap / category / name
    item.mkdir(parents=True)
    number = name.split("-")[1]
    (item / f"{name}.md").write_text(
        f"# BE-{number} — {title}\n\n"
        "<!-- BE-METADATA -->\n"
        f"| Field | Value |\n|---|---|\n| Status | **{status}** |\n"
        "<!-- /BE-METADATA -->\n\n"
        f"## Introduction\n\n{intro}\n\n"
        "## Motivation\n\nwhy.\n",
        encoding="utf-8",
    )
    (item / f"{name}-ja.md").write_text(
        f"# BE-{number} — {title}\n\n* 状態: **{status}**\n", encoding="utf-8"
    )
    return item


# --- scan ------------------------------------------------------------------------


def test_scan_reads_id_slug_status_title_and_intro(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(
        roadmap, "proposals", "BE-9001-foo-bar", "Proposal", title="A thing", intro="Do the thing."
    )
    (item,) = sync.scan_items(roadmap)
    assert (item.be_id, item.slug, item.category, item.status) == (
        "BE-9001",
        "foo-bar",
        "proposals",
        "Proposal",
    )
    assert item.title == "A thing"
    assert item.intro == "Do the thing."


def test_scan_skips_placeholder_and_statusless(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-real", "Proposal")
    # A BE-XXXX placeholder has no permanent number; it must never be scanned.
    placeholder = roadmap / "proposals" / "BE-XXXX-draft"
    placeholder.mkdir(parents=True)
    (placeholder / "BE-XXXX-draft.md").write_text("# BE-XXXX — draft\n", encoding="utf-8")
    # A numbered item with no readable Status is skipped (nothing to reconcile against).
    nostatus = roadmap / "proposals" / "BE-9002-nostatus"
    nostatus.mkdir(parents=True)
    (nostatus / "BE-9002-nostatus.md").write_text("# BE-9002 — x\n\nno status\n", encoding="utf-8")
    assert [i.be_id for i in sync.scan_items(roadmap)] == ["BE-9001"]


def test_scan_sorts_by_id_across_categories(tmp_path: Path) -> None:
    # Items are written to different category folders in an order that would NOT sort by id if
    # the scan merely concatenated per-category results in CATEGORIES order.
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "deferred", "BE-9001-earliest", "Proposal (deferred)")
    _write_item(roadmap, "implemented", "BE-9003-latest", "Implemented")
    _write_item(roadmap, "proposals", "BE-9002-middle", "Proposal")
    assert [i.be_id for i in sync.scan_items(roadmap)] == ["BE-9001", "BE-9002", "BE-9003"]


def test_parse_english_missing_file_is_empty(tmp_path: Path) -> None:
    assert sync._parse_english(tmp_path / "proposals" / "BE-9999-gone") == ("", "", None)


# --- plan (the pure lifecycle rule) ----------------------------------------------


def _item(be_id: str, status: str) -> object:
    return sync.Item(
        be_id=be_id, slug="s", category="proposals", status=status, title="t", intro=""
    )


def test_plan_creates_for_open_items_without_an_issue() -> None:
    items = [_item("BE-0001", "Proposal"), _item("BE-0002", "In progress")]
    result = sync.plan(items, existing_open_ids=set())
    assert [i.be_id for i in result.to_create] == ["BE-0001", "BE-0002"]
    assert result.to_close == []


def test_plan_is_a_noop_when_open_item_already_has_an_issue() -> None:
    items = [_item("BE-0001", "Proposal")]
    result = sync.plan(items, existing_open_ids={"BE-0001"})
    assert result.to_create == []
    assert result.to_close == []


def test_plan_closes_shipped_or_shelved_items_with_an_open_issue() -> None:
    items = [_item("BE-0001", "Implemented"), _item("BE-0002", "Proposal (deferred)")]
    result = sync.plan(items, existing_open_ids={"BE-0001", "BE-0002"})
    assert result.to_create == []
    assert result.to_close == ["BE-0001", "BE-0002"]


def test_plan_leaves_orphan_issue_ids_alone() -> None:
    # An open issue whose id matches no item in the tree is not closed — the rule keys off an
    # item's Status, and there's no item (so no Status) to act on. The unrelated open item still
    # gets its issue created; only the orphan close is suppressed.
    result = sync.plan([_item("BE-0001", "Implemented")], existing_open_ids={"BE-0404"})
    assert result.to_create == []
    assert result.to_close == []


# --- issue shape -----------------------------------------------------------------


def test_issue_body_links_back_and_quotes_intro() -> None:
    item = sync.Item(
        be_id="BE-0042",
        slug="widget",
        category="in-progress",
        status="In progress",
        title="A widget",
        intro="Line one.\n\nLine two.",
    )
    body = sync.issue_body(item)
    assert "roadmaps/in-progress/BE-0042-widget/BE-0042-widget.md" in body
    assert "[BE-0042]" in body
    assert "> Line one." in body
    assert "> Line two." in body
    # The blank line between the two intro paragraphs is quoted as a bare ">".
    assert "\n>\n" in body


# --- gh seams (parsing / error handling, no network) -----------------------------


def test_existing_open_issues_parses_ids_and_ignores_issues_without_a_be_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        [
            {"number": 12, "title": "[BE-0007] something"},
            {"number": 13, "title": "a manual issue with no BE id"},
        ]
    )
    monkeypatch.setattr(sync, "_gh", lambda args, capture=False: payload)
    assert sync.existing_open_issues() == {"BE-0007": 12}


def test_ensure_label_tolerates_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="label already exists; ..."
        )

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    sync.ensure_label()  # must not raise


def test_ensure_label_reraises_other_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="HTTP 403: forbidden"
        )

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        sync.ensure_label()


# --- sync / check orchestration --------------------------------------------------


def test_sync_creates_and_closes_via_seams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-open", "Proposal")  # open, no issue -> create
    _write_item(
        roadmap, "implemented", "BE-9002-done", "Implemented"
    )  # shipped, has issue -> close
    created: list[str] = []
    closed: list[int] = []
    labels: list[bool] = []
    monkeypatch.setattr(sync, "existing_open_issues", lambda: {"BE-9002": 55})
    monkeypatch.setattr(sync, "ensure_label", lambda: labels.append(True))
    monkeypatch.setattr(sync, "create_issue", lambda item: created.append(item.be_id))
    monkeypatch.setattr(sync, "close_issue", lambda number: closed.append(number))
    result = sync.sync(roadmap)
    assert [i.be_id for i in result.to_create] == ["BE-9001"]
    assert result.to_close == ["BE-9002"]
    assert created == ["BE-9001"]
    assert closed == [55]
    assert labels == [True]  # label ensured only because something is created


def test_sync_noop_does_not_ensure_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "implemented", "BE-9002-done", "Implemented")  # closed, no open issue
    monkeypatch.setattr(sync, "existing_open_issues", dict)
    monkeypatch.setattr(
        sync, "ensure_label", lambda: pytest.fail("label must not be touched on a no-op")
    )
    monkeypatch.setattr(sync, "create_issue", lambda item: pytest.fail("nothing to create"))
    monkeypatch.setattr(sync, "close_issue", lambda number: pytest.fail("nothing to close"))
    result = sync.sync(roadmap)
    assert result.to_create == [] and result.to_close == []


def test_check_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-open", "Proposal")
    monkeypatch.setattr(sync, "ROADMAP", roadmap)
    monkeypatch.setattr(sync, "existing_open_issues", dict)
    assert sync.main(["--check"]) == 1  # BE-9001 open but no issue -> drift
    monkeypatch.setattr(sync, "existing_open_issues", lambda: {"BE-9001": 1})
    assert sync.main(["--check"]) == 0  # now consistent


def test_main_sync_runs_without_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-open", "Proposal")
    monkeypatch.setattr(sync, "ROADMAP", roadmap)
    monkeypatch.setattr(sync, "existing_open_issues", lambda: {"BE-9001": 1})  # already consistent
    monkeypatch.setattr(sync, "ensure_label", lambda: None)
    monkeypatch.setattr(sync, "create_issue", lambda item: None)
    monkeypatch.setattr(sync, "close_issue", lambda number: None)
    assert sync.main([]) == 0

"""The no-database `LocalProjectRegistry` (BE-0225 unit 2): the on-disk JSON store that gives a
single-user local `serve` the same project listing/switching and per-project run partitioning the
DB-backed registry gets from the `runs.project_id` column. Everything here runs on the Linux gate —
no Simulator, no Postgres — against a real JSON file under ``tmp_path`` (no mocks)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bajutsu.serve.project_registry import LocalProjectRegistry


def test_add_then_get_and_list_a_project(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    source = {"kind": "file", "locator": {"path": "checkout.yaml"}}

    added = reg.add(org_id="default", name="checkout", source=source)

    assert added.name == "checkout"
    assert added.source == source
    assert added.id  # a generated, non-empty id
    got = reg.get(org_id="default", name="checkout")
    assert got is not None and got.id == added.id
    assert [p.name for p in reg.list_projects(org_id="default")] == ["checkout"]


def test_get_returns_none_for_an_unknown_project(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    assert reg.get(org_id="default", name="absent") is None
    assert reg.list_projects(org_id="default") == []


def test_add_is_idempotent_by_name_and_rebinds_the_source(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    first = reg.add(
        org_id="default", name="checkout", source={"kind": "file", "locator": {"path": "a.yaml"}}
    )

    rebound = reg.add(
        org_id="default", name="checkout", source={"kind": "file", "locator": {"path": "b.yaml"}}
    )

    # Re-adding the same (org, name) reuses the id and rebinds the source, never duplicating.
    assert rebound.id == first.id
    assert rebound.source == {"kind": "file", "locator": {"path": "b.yaml"}}
    assert [p.name for p in reg.list_projects(org_id="default")] == ["checkout"]


def test_projects_are_scoped_and_ordered_by_name(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    reg.add(org_id="default", name="zebra", source=None)
    reg.add(org_id="default", name="alpha", source=None)
    reg.add(org_id="other", name="hidden", source=None)

    assert [p.name for p in reg.list_projects(org_id="default")] == ["alpha", "zebra"]
    assert [p.name for p in reg.list_projects(org_id="other")] == ["hidden"]


def test_remove_deregisters_the_project(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    reg.add(org_id="default", name="checkout", source=None)

    reg.remove(org_id="default", name="checkout")

    assert reg.get(org_id="default", name="checkout") is None
    assert reg.list_projects(org_id="default") == []


def test_no_active_project_until_one_is_set(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    reg.add(org_id="default", name="checkout", source=None)
    assert reg.resolve_active(org_id="default") is None


def test_set_and_resolve_the_active_project(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    reg.add(org_id="default", name="checkout", source=None)
    reg.add(org_id="default", name="settings", source=None)

    reg.set_active(org_id="default", name="settings")

    active = reg.resolve_active(org_id="default")
    assert active is not None and active.name == "settings"


def test_set_active_ignores_an_unknown_project(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    reg.set_active(org_id="default", name="absent")
    assert reg.resolve_active(org_id="default") is None


def test_removing_the_active_project_clears_active(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    reg.add(org_id="default", name="checkout", source=None)
    reg.set_active(org_id="default", name="checkout")

    reg.remove(org_id="default", name="checkout")

    assert reg.resolve_active(org_id="default") is None


def test_tag_run_partitions_runs_by_project_newest_first(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    p = reg.add(org_id="default", name="checkout", source=None)

    reg.tag_run(org_id="default", project_id=p.id, run_id="run-1")
    reg.tag_run(org_id="default", project_id=p.id, run_id="run-2")

    assert reg.run_ids(org_id="default", project_id=p.id) == ["run-2", "run-1"]


def test_tag_run_is_idempotent(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    p = reg.add(org_id="default", name="checkout", source=None)

    reg.tag_run(org_id="default", project_id=p.id, run_id="run-1")
    reg.tag_run(org_id="default", project_id=p.id, run_id="run-1")

    assert reg.run_ids(org_id="default", project_id=p.id) == ["run-1"]


def test_remove_unlabels_the_projects_runs(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    p = reg.add(org_id="default", name="checkout", source=None)
    reg.tag_run(org_id="default", project_id=p.id, run_id="run-1")

    reg.remove(org_id="default", name="checkout")

    # Parity with the DB path's ON DELETE SET NULL: the run itself lives on under the runs/ tree,
    # but a per-project listing no longer surfaces it — the project label is gone.
    assert reg.run_ids(org_id="default", project_id=p.id) == []


def test_run_index_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    reg = LocalProjectRegistry(path)
    p = reg.add(org_id="default", name="checkout", source=None)
    reg.tag_run(org_id="default", project_id=p.id, run_id="run-1")
    reg.set_active(org_id="default", name="checkout")

    reopened = LocalProjectRegistry(path)
    assert reopened.run_ids(org_id="default", project_id=p.id) == ["run-1"]
    active = reopened.resolve_active(org_id="default")
    assert active is not None and active.name == "checkout"


def test_state_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    LocalProjectRegistry(path).add(
        org_id="default", name="checkout", source={"kind": "file", "locator": {"path": "a.yaml"}}
    )

    # A fresh instance over the same file reads back what the first one saved.
    reopened = LocalProjectRegistry(path)
    got = reopened.get(org_id="default", name="checkout")
    assert got is not None
    assert got.source == {"kind": "file", "locator": {"path": "a.yaml"}}


def test_add_with_no_source_preserves_an_existing_binding(tmp_path: Path) -> None:
    """Parity with the DB backend behind the same seam: `SqlProjectRegistry.add` → `create_project`
    only writes `source` when it is non-None, so a rename-only re-add can't wipe an existing
    binding. The local store must hold the same contract — re-adding with `source=None` keeps the
    stored source rather than clobbering it to null."""
    path = tmp_path / "projects.json"
    reg = LocalProjectRegistry(path)
    reg.add(
        org_id="default", name="checkout", source={"kind": "file", "locator": {"path": "a.yaml"}}
    )

    reg.add(org_id="default", name="checkout", source=None)

    got = reg.get(org_id="default", name="checkout")
    assert got is not None
    assert got.source == {"kind": "file", "locator": {"path": "a.yaml"}}


def test_a_malformed_store_falls_back_to_empty_and_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt or unreadable store must not crash serve boot — but wiping the hub silently on
    every subsequent boot is worse. It resets to empty (determinism-first: the operator
    re-registers) *and* logs a warning, matching the "loud, not silent" fallback the sibling
    provider-settings store uses. A first boot with no file yet stays silent (not a corruption)."""
    path = tmp_path / "projects.json"
    path.write_text("{ not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        reg = LocalProjectRegistry(path)

    assert reg.list_projects(org_id="default") == []
    assert any("project" in r.message.lower() for r in caplog.records)


def test_a_first_boot_with_no_store_is_silent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        reg = LocalProjectRegistry(tmp_path / "projects.json")

    assert reg.list_projects(org_id="default") == []
    assert caplog.records == []

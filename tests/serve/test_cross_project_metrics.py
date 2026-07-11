"""Cross-project comparison composition (BE-0226 unit 1) over real registry + artifact stores."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu.serve.artifacts import LocalArtifactStore
from bajutsu.serve.operations.project_comparison import compare_projects
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.project_registry import LocalProjectRegistry


def _write_manifest(runs_dir: Path, run_id: str, *, ok: bool) -> None:
    """A manifest.json as the runner writes it — only the fields the aggregator reads."""
    d = runs_dir / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "scenarios": [{"scenario": "s", "ok": ok, "duration_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )


def test_compare_projects_one_row_per_project_with_project_scoped_runs(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_manifest(runs_dir, "20260101-000000", ok=True)
    _write_manifest(runs_dir, "20260102-000000", ok=False)
    _write_manifest(runs_dir, "20260103-000000", ok=True)

    registry = LocalProjectRegistry(tmp_path / "projects.json")
    checkout = registry.add(org_id=DEFAULT_ORG, name="checkout", source=None)
    search = registry.add(org_id=DEFAULT_ORG, name="search", source=None)
    # checkout owns two runs (one failing → 0.5), search owns one (passing → 1.0).
    registry.tag_run(org_id=DEFAULT_ORG, project_id=checkout.id, run_id="20260101-000000")
    registry.tag_run(org_id=DEFAULT_ORG, project_id=checkout.id, run_id="20260102-000000")
    registry.tag_run(org_id=DEFAULT_ORG, project_id=search.id, run_id="20260103-000000")

    store = LocalArtifactStore(runs_dir)
    rows = compare_projects(registry, store, org=DEFAULT_ORG)

    # One row per registered project, ordered by the registry (name order).
    assert [r.name for r in rows] == ["checkout", "search"]
    assert rows[0].project_id == checkout.id
    assert rows[0].runs == 2
    assert rows[0].pass_rate == 0.5
    assert rows[1].runs == 1
    assert rows[1].pass_rate == 1.0


def test_compare_projects_unrun_project_is_a_blank_row(tmp_path: Path) -> None:
    registry = LocalProjectRegistry(tmp_path / "projects.json")
    registry.add(org_id=DEFAULT_ORG, name="fresh", source=None)

    store = LocalArtifactStore(tmp_path / "runs")
    rows = compare_projects(registry, store, org=DEFAULT_ORG)

    assert len(rows) == 1
    assert rows[0].runs == 0
    assert rows[0].pass_rate == 0.0
    assert rows[0].trend == []


def test_compare_projects_no_projects_is_empty(tmp_path: Path) -> None:
    registry = LocalProjectRegistry(tmp_path / "projects.json")
    store = LocalArtifactStore(tmp_path / "runs")
    assert compare_projects(registry, store, org=DEFAULT_ORG) == []

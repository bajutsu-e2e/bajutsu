"""Manifest bridge and renderers for the cross-run flakiness surface (BE-0220, Half 1).

`records_from_manifests` lets the file-backed `--history` CLI path and the local (no-DB) serve
path feed `rank_flakiness` identically to the DB path, and `render` / `render_html` turn the
ranked report into the CLI text and the self-contained serve panel. All read-only and AI-free.
"""

from __future__ import annotations

from datetime import UTC, datetime

from bajutsu.serve.flakiness import (
    rank_flakiness,
    records_from_manifests,
    render,
    render_html,
)


def _manifest(
    run_id: str,
    *,
    ok: bool,
    scenario_hash: str | None = "sha256:a",
    name: str = "login",
) -> dict[str, object]:
    """A run manifest as the runner writes it — run-level `ok`, a provenance stamp, one scenario."""
    m: dict[str, object] = {
        "runId": run_id,
        "ok": ok,
        "scenarios": [{"scenario": name, "ok": ok}],
    }
    if scenario_hash is not None:
        m["provenance"] = {"scenarioHash": scenario_hash}
    return m


def test_records_from_manifests_carries_grouping_fields() -> None:
    (record,) = records_from_manifests([_manifest("20260101-000000", ok=True)])
    assert record.id == "20260101-000000"
    assert record.ok is True
    assert record.scenario_hash == "sha256:a"
    assert record.summary["scenarios"] == ["login"]
    # The id is a bare timestamp, so created_at is recovered for windowing (parse_run_id_timestamp).
    assert record.created_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_records_from_manifests_reproduce_flaky_ranking() -> None:
    manifests = [
        _manifest("20260101-000000", ok=True),
        _manifest("20260102-000000", ok=True),
        _manifest("20260103-000000", ok=False),
    ]
    report = rank_flakiness(records_from_manifests(manifests))
    (s,) = report.scenarios
    assert s.classification == "flaky"
    assert (s.runs, s.passed, s.failed) == (3, 2, 1)
    # Representative newest passing / failing runs for evidence linking.
    assert s.representative_pass_run_id == "20260102-000000"
    assert s.representative_fail_run_id == "20260103-000000"


def test_records_from_manifests_missing_provenance_is_ungroupable() -> None:
    # No provenance → scenario_hash None → rank_flakiness skips it (mirrors audit --history).
    (record,) = records_from_manifests([_manifest("20260101-000000", ok=True, scenario_hash=None)])
    assert record.scenario_hash is None
    report = rank_flakiness([record])
    assert report.scenarios == []
    assert report.skipped == 1


def test_render_text_lists_flaky_first_with_counts() -> None:
    report = rank_flakiness(
        records_from_manifests(
            [
                _manifest("20260101-000000", ok=True),
                _manifest("20260102-000000", ok=False),
            ]
        )
    )
    text = render(report)
    assert "login" in text
    assert "flaky" in text
    assert "1 passed" in text and "1 failed" in text


def test_render_text_empty() -> None:
    assert "no" in render(rank_flakiness([])).lower()


def test_render_html_is_self_contained_with_links() -> None:
    report = rank_flakiness(
        records_from_manifests(
            [
                _manifest("20260101-000000", ok=True),
                _manifest("20260102-000000", ok=False),
            ]
        )
    )
    html = render_html(report)
    assert html.startswith("<!DOCTYPE html>")
    assert "login" in html
    assert "flaky" in html
    # Rows link to the representative passing / failing runs' evidence.
    assert "/runs/20260101-000000/report.html" in html
    assert "/runs/20260102-000000/report.html" in html
    # New-tab evidence links carry rel="noopener", like every other target="_blank" in the codebase.
    assert 'target="_blank" rel="noopener"' in html
    assert 'target="_blank">' not in html

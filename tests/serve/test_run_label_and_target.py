"""BE-0404: the run-history label and the target stamp that replace the project layer.

Units 2 and 3 are what make a restart between two configs readable apart, and "the Android target
passes while the iOS target fails" computable from stored data. Unit 4 reads both back: the label
filter over the run list, and the per-target comparison. Everything here is deterministic and
AI-free — a label is metadata attached to a run, never an input to a verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from _shared import project, write_run
from sqlalchemy import Engine

from bajutsu import serve as srv
from bajutsu.report.manifest import MAX_LABEL_LENGTH, manifest_dict
from bajutsu.serve.operations.config import launch_label
from bajutsu.serve.operations.dispatch import _run_label
from bajutsu.serve.operations.reads import (
    ALL_LABELS,
    apply_label_filter,
    flakiness_html,
    runs_payload,
    stats_html,
)
from bajutsu.serve.operations.target_comparison import compare_targets

# --- unit 2: the default label, and the operator's override ---


def test_launch_label_names_a_local_config_by_its_file_stem(tmp_path: Path) -> None:
    assert launch_label(tmp_path / "checkout.config.yaml", None) == "checkout.config"


def test_launch_label_disambiguates_two_configs_from_one_repository() -> None:
    # Naming a Git-materialized config for its repository alone folded two configs of one repo onto
    # one label — the collision an explicit project name used to resolve. The in-repo config path
    # is what keeps them apart.
    one = launch_label(Path("x.yaml"), {"repo": "shop", "path": "web/bajutsu.config.yaml"})
    two = launch_label(Path("x.yaml"), {"repo": "shop", "path": "ios/bajutsu.config.yaml"})
    assert one != two
    # A stamp carrying no path still labels by the repository rather than producing a trailing slash.
    assert launch_label(Path("x.yaml"), {"repo": "shop"}) == "shop"


def test_the_label_defaults_to_the_bound_config_and_the_body_overrides_it(tmp_path: Path) -> None:
    _scn, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    assert _run_label(state, {}) == ("bajutsu.config", None)
    assert _run_label(state, {"label": "nightly"}) == ("nightly", None)
    # A blank override is not a label — fall back to the default rather than recording "".
    assert _run_label(state, {"label": "   "}) == ("bajutsu.config", None)


def test_an_oversized_label_is_refused_rather_than_truncated(tmp_path: Path) -> None:
    # An operator must learn the label was refused, instead of finding a silently shortened one in
    # the history that no longer matches what they filter on.
    _scn, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    label, err = _run_label(state, {"label": "x" * (MAX_LABEL_LENGTH + 1)})
    assert label is None
    assert err is not None and err[1] == 400
    label, err = _run_label(state, {"label": 7})
    assert label is None
    assert err is not None and err[1] == 400


def test_a_deployment_with_no_bound_config_records_no_label(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    assert _run_label(state, {}) == (None, None)


# --- unit 3: the target stamp ---


def test_the_manifest_records_the_target_and_the_label() -> None:
    data = manifest_dict("r1", [], target="ios", label="checkout")
    assert data["target"] == "ios"
    assert data["label"] == "checkout"
    # Absent rather than null when the run named neither, so an older reader sees the same shape it
    # always did.
    assert "target" not in manifest_dict("r1", [])
    assert "label" not in manifest_dict("r1", [])


# --- unit 4: reading by label ---


def _labelled(tmp_path: Path) -> srv.ServeState:
    _scn, cfg, runs = project(tmp_path)
    write_run(runs, "20260101-1", ok=True, scenarios=[("alpha", True)], label="bajutsu.config")
    write_run(runs, "20260101-2", ok=True, scenarios=[("alpha", True)], label="other.config")
    return srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)


def test_the_run_list_defaults_to_the_bound_config_s_partition(tmp_path: Path) -> None:
    rows, status = runs_payload(_labelled(tmp_path))
    assert status == 200
    assert [r["id"] for r in rows] == ["20260101-1"]


def test_an_explicit_label_selects_another_partition_and_the_star_restores_all(
    tmp_path: Path,
) -> None:
    state = _labelled(tmp_path)
    assert [r["id"] for r in runs_payload(state, label="other.config")[0]] == ["20260101-2"]
    assert len(runs_payload(state, label=ALL_LABELS)[0]) == 2


def test_an_unlabeled_history_opens_unfiltered_rather_than_empty(tmp_path: Path) -> None:
    # A deployment whose history predates the label has no run carrying the bound one. Filtering it
    # to nothing would hide the whole history behind a filter the reader cannot see.
    _scn, cfg, runs = project(tmp_path)
    write_run(runs, "20260101-1", ok=True, scenarios=[("alpha", True)])
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    assert [r["id"] for r in runs_payload(state)[0]] == ["20260101-1"]


def test_an_unlabeled_run_stays_visible_once_labeled_runs_arrive(tmp_path: Path) -> None:
    # An unlabeled run belongs to no partition: it predates the column, or was enqueued with no
    # config bound. Without this it would drop out of the default view the moment the deployment
    # records its first labeled run — a history that vanishes on upgrade, which is the outcome the
    # dropped backfill would otherwise have caused.
    _scn, cfg, runs = project(tmp_path)
    write_run(runs, "20260101-1", ok=True, scenarios=[("alpha", True)])
    write_run(runs, "20260101-2", ok=True, scenarios=[("alpha", True)], label="bajutsu.config")
    write_run(runs, "20260101-3", ok=True, scenarios=[("alpha", True)], label="other.config")
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    assert [r["id"] for r in runs_payload(state)[0]] == ["20260101-2", "20260101-1"]


def test_the_filter_is_a_no_op_without_a_bound_config(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    rows = [{"id": "a", "label": "x"}, {"id": "b", "label": "y"}]
    assert apply_label_filter(state, rows, None) == rows


# --- unit 4: comparing by target ---


def test_the_comparison_ranks_every_declared_target_including_an_unrun_one(
    tmp_path: Path,
) -> None:
    _scn, cfg, runs = project(tmp_path)
    write_run(runs, "20260101-1", ok=True, scenarios=[("alpha", True)], target="demo")
    write_run(runs, "20260101-2", ok=False, scenarios=[("alpha", False)], target="demo")
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    rows = compare_targets(state, org="default")
    # Declaration order, and `other` charts as a blank row rather than dropping out of the ranking.
    assert [r.name for r in rows] == ["demo", "other"]
    assert rows[0].runs == 2
    assert rows[0].pass_rate == 0.5
    assert rows[1].runs == 0


def test_the_comparison_is_empty_with_no_config_bound(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    assert compare_targets(state, org="default") == []


def test_a_partition_is_not_truncated_by_the_global_window(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The label is a post-filter, so capping the query first would drop a run of the bound config
    # that falls outside the newest-N global window — leaving a partition's history shorter than the
    # window it claims to show. The interleaving this item exists to fix is exactly the shape that
    # pushes a config's runs past that boundary.
    from bajutsu.serve.operations.reads import RUN_WINDOW
    from bajutsu.serve.server.db import RunRecord, SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    repository = SqlRepository(engine)
    repository.ensure_org("default", slug="default", name="default")
    _scn, cfg, runs = project(tmp_path)
    # One run of the bound config, then a full window's worth of the other config's newer runs.
    repository.record_run(
        RunRecord(
            id="20260101-0",
            org_id="default",
            status="done",
            ok=True,
            label="bajutsu.config",
            summary={"id": "20260101-0", "ok": True, "label": "bajutsu.config"},
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    for i in range(RUN_WINDOW):
        repository.record_run(
            RunRecord(
                id=f"20260102-{i}",
                org_id="default",
                status="done",
                ok=True,
                label="other.config",
                summary={"id": f"20260102-{i}", "ok": True, "label": "other.config"},
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path, repository=repository)

    rows = runs_payload(state)[0]
    assert [r["id"] for r in rows] == ["20260101-0"]


def test_flaky_reads_the_bound_partition_on_both_backends(tmp_path: Path) -> None:
    # A flakiness score computed across two configs' interleaved histories is the same defect the
    # label exists to fix, so the panel reads one partition — and the artifact-store backend must
    # agree with the database one about which.
    from bajutsu.serve.operations.reads import _flakiness_report

    _scn, cfg, runs = project(tmp_path)
    write_run(runs, "20260101-1", ok=True, scenarios=[("alpha", True)], label="bajutsu.config")
    write_run(runs, "20260101-2", ok=False, scenarios=[("alpha", False)], label="other.config")
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    # The ranking sees one run — the bound config's — not both configs' interleaved. (These runs
    # carry no provenance fingerprint, so they land in `skipped`; the count is the partition.)
    assert _flakiness_report(state, None).skipped == 1
    # Opened to every label, both configs' runs are back in one history.
    assert _flakiness_report(state, None, ALL_LABELS).skipped == 2
    # The rendered panel goes through the same seam, so it is enough to pin that it renders.
    assert flakiness_html(state)[1] == 200


def test_an_unlabeled_run_stays_visible_on_the_database_path(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The database half of the same guarantee: the query itself must admit a null label, since the
    # partition is pushed into it rather than post-filtered.
    from bajutsu.serve.server.db import RunRecord, SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    repository = SqlRepository(engine)
    repository.ensure_org("default", slug="default", name="default")
    _scn, cfg, runs = project(tmp_path)
    for run_id, label in (("r-old", None), ("r-mine", "bajutsu.config"), ("r-other", "other")):
        repository.record_run(
            RunRecord(
                id=run_id,
                org_id="default",
                status="done",
                ok=True,
                label=label,
                summary={"id": run_id, "ok": True, "label": label or ""},
            )
        )
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path, repository=repository)

    assert {r["id"] for r in runs_payload(state)[0]} == {"r-old", "r-mine"}
    assert {r["id"] for r in runs_payload(state, label=ALL_LABELS)[0]} == {
        "r-old",
        "r-mine",
        "r-other",
    }
    # `r-old` carries no label, so it matches every partition — including one no run was recorded
    # under. The fallback below is what a history of *only* labeled runs needs.
    assert {r["id"] for r in runs_payload(state, label="never-used")[0]} == {"r-old"}


def test_a_label_matching_nothing_opens_the_whole_history(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A reader is never left staring at an empty page with no filter visible to clear, so a
    # partition that matches no run at all falls back to the unfiltered history.
    from bajutsu.serve.server.db import RunRecord, SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    repository = SqlRepository(engine)
    repository.ensure_org("default", slug="default", name="default")
    _scn, cfg, runs = project(tmp_path)
    for run_id in ("r-a", "r-b"):
        write_run(runs, run_id, ok=True, scenarios=[("alpha", True)], label="other.config")
        repository.record_run(
            RunRecord(
                id=run_id,
                org_id="default",
                status="done",
                ok=True,
                label="other.config",
                summary={"id": run_id, "ok": True, "label": "other.config"},
            )
        )
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path, repository=repository)

    assert {r["id"] for r in runs_payload(state)[0]} == {"r-a", "r-b"}
    # The run-stats dashboard reads through the same partition and the same fallback, so it
    # aggregates those two runs rather than reporting nothing to aggregate.
    assert "nothing to aggregate" not in stats_html(state)[0]
    # Flaky reads the same partition through the same fallback, so the three history-backed views
    # never disagree about what this deployment ran.
    from bajutsu.serve.operations.reads import _flakiness_report

    assert _flakiness_report(state, None).skipped == 2
    assert flakiness_html(state)[1] == 200

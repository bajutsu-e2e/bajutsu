"""Aggregate run-stats dashboard (BE-0102) — the deterministic aggregator over run manifests."""

from __future__ import annotations

from typing import Any

from bajutsu.analysis import stats as _stats


def _manifest(
    run_id: str,
    *,
    ok: bool | None = None,
    scenario_hash: str | None = None,
    backend: str = "",
    scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A minimal manifest.json mapping — only the fields the aggregator reads."""
    scen = scenarios if scenarios is not None else [{"scenario": "s", "ok": bool(ok)}]
    m: dict[str, Any] = {
        "runId": run_id,
        "ok": all(s.get("ok") for s in scen) if ok is None else ok,
        "scenarios": scen,
    }
    if backend:
        m["backend"] = backend
    if scenario_hash is not None:
        m["provenance"] = {"scenarioHash": scenario_hash}
    return m


def test_pass_rate_over_runs() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest("20260101-000000", ok=True),
            _manifest("20260102-000000", ok=False),
            _manifest("20260103-000000", ok=True),
        ]
    )
    assert stats.runs == 3
    assert stats.passed_runs == 2
    assert stats.failed_runs == 1
    assert stats.pass_rate == 2 / 3


def test_empty_run_set() -> None:
    stats = _stats.aggregate_runs([])
    assert stats.runs == 0
    assert stats.pass_rate == 0.0
    assert stats.by_run == []
    assert stats.scenarios == []


def test_non_manifests_and_missing_scenarios_are_ignored() -> None:
    stats = _stats.aggregate_runs(
        [
            "not a manifest",  # type: ignore[list-item]
            {"runId": "x", "ok": True},  # no scenarios list
            _manifest("20260101-000000", ok=True),
        ]
    )
    assert stats.runs == 1


def test_by_run_is_chronological_and_carries_the_point() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest("20260103-000000", ok=True, backend="fake"),
            _manifest("20260101-000000", ok=False, backend="xcuitest"),
        ]
    )
    assert [p.run_id for p in stats.by_run] == ["20260101-000000", "20260103-000000"]
    first = stats.by_run[0]
    assert first.day == "2026-01-01"
    assert first.ok is False
    assert first.backend == "xcuitest"


def test_by_day_rolls_up_pass_rate() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest("20260101-090000", ok=True),
            _manifest("20260101-100000", ok=False),
            _manifest("20260102-090000", ok=True),
        ]
    )
    by_day = {d.day: d for d in stats.by_day}
    assert by_day["2026-01-01"].runs == 2
    assert by_day["2026-01-01"].pass_rate == 0.5
    assert by_day["2026-01-02"].pass_rate == 1.0


def test_custom_run_id_has_no_day() -> None:
    stats = _stats.aggregate_runs([_manifest("audit-run", ok=True)])
    assert stats.by_run[0].day == ""
    assert stats.by_day[0].day == ""


def test_volume_by_backend() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest("20260101-000000", ok=True, backend="xcuitest"),
            _manifest("20260102-000000", ok=True, backend="xcuitest"),
            _manifest("20260103-000000", ok=True, backend="fake"),
        ]
    )
    assert stats.by_backend == {"fake": 1, "xcuitest": 2}


def test_run_duration_sums_scenario_durations() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenarios=[
                    {"scenario": "a", "ok": True, "duration_s": 1.5},
                    {"scenario": "b", "ok": True, "duration_s": 2.0},
                ],
            )
        ]
    )
    assert stats.by_run[0].duration_s == 3.5
    assert stats.total_duration_s == 3.5


def test_scenario_stat_folds_duration_and_flakiness() -> None:
    # Same fingerprint, verdict flips across runs → flaky (BE-0049 classifier, reused).
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenario_hash="sha256:aaa",
                scenarios=[{"scenario": "login", "ok": True, "duration_s": 2.0}],
            ),
            _manifest(
                "20260102-000000",
                scenario_hash="sha256:aaa",
                scenarios=[{"scenario": "login", "ok": False, "duration_s": 4.0}],
            ),
        ]
    )
    assert len(stats.scenarios) == 1
    sc = stats.scenarios[0]
    assert sc.name == "login"
    assert sc.scenario_hash == "sha256:aaa"
    assert sc.runs == 2
    assert sc.pass_rate == 0.5
    assert sc.classification == "flaky"
    assert sc.avg_duration_s == 3.0
    assert sc.max_duration_s == 4.0


def test_edited_scenario_starts_a_new_series() -> None:
    # A content edit gives a new fingerprint, so it is a separate series, not a flake.
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenario_hash="sha256:aaa",
                scenarios=[{"scenario": "login", "ok": True}],
            ),
            _manifest(
                "20260102-000000",
                scenario_hash="sha256:bbb",
                scenarios=[{"scenario": "login", "ok": False}],
            ),
        ]
    )
    hashes = {sc.scenario_hash for sc in stats.scenarios}
    assert hashes == {"sha256:aaa", "sha256:bbb"}


def test_runs_without_fingerprint_are_skipped_for_scenario_series() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest("20260101-000000", ok=True),  # no scenarioHash
            _manifest(
                "20260102-000000",
                scenario_hash="sha256:aaa",
                scenarios=[{"scenario": "s", "ok": True}],
            ),
        ]
    )
    assert stats.scenarios_skipped == 1
    assert len(stats.scenarios) == 1
    # The un-fingerprinted run still counts toward the run-level trend.
    assert stats.runs == 2


def test_failing_scenarios_ranked_with_top_reason() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenarios=[{"scenario": "checkout", "ok": False, "failure": "timeout"}],
            ),
            _manifest(
                "20260102-000000",
                scenarios=[{"scenario": "checkout", "ok": False, "failure": "timeout"}],
            ),
            _manifest(
                "20260103-000000",
                scenarios=[{"scenario": "search", "ok": False, "failure": "no match"}],
            ),
        ]
    )
    assert stats.failing_scenarios[0].key == "checkout"
    assert stats.failing_scenarios[0].failures == 2
    assert stats.failing_scenarios[0].reason == "timeout"
    assert stats.failing_scenarios[1].key == "search"


def test_failing_steps_keyed_by_scenario_and_action() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenarios=[
                    {
                        "scenario": "login",
                        "ok": False,
                        "steps": [
                            {"index": 0, "action": "tap", "ok": True},
                            {"index": 1, "action": "type", "ok": False, "reason": "not found"},
                        ],
                    }
                ],
            )
        ]
    )
    assert stats.failing_steps == [
        _stats.Hotspot(
            key="login > type", failures=1, reason="not found", run_ids=("20260101-000000",)
        )
    ]


def test_failing_assertions_span_step_and_scenario_level() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenarios=[
                    {
                        "scenario": "s",
                        "ok": False,
                        "expect_results": [
                            {"ok": False, "kind": "text", "reason": "missing"},
                        ],
                        "steps": [
                            {
                                "index": 0,
                                "action": "tap",
                                "ok": False,
                                "assertion_results": [
                                    {"ok": False, "kind": "text", "reason": "missing"},
                                ],
                            }
                        ],
                    }
                ],
            )
        ]
    )
    assert stats.failing_assertions == [
        _stats.Hotspot(key="text", failures=2, reason="missing", run_ids=("20260101-000000",))
    ]


def test_hotspot_run_ids_are_sorted_deduped_across_runs() -> None:
    # BE-0241: a hotspot links back to every run it failed in — sorted, and one id per run even when
    # a scenario fails more than once inside it (here two failing steps in the same run).
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260102-000000",
                scenarios=[{"scenario": "checkout", "ok": False, "failure": "timeout"}],
            ),
            _manifest(
                "20260101-000000",
                scenarios=[
                    {
                        "scenario": "checkout",
                        "ok": False,
                        "failure": "timeout",
                        "steps": [
                            {"index": 0, "action": "tap", "ok": False, "reason": "x"},
                            {"index": 1, "action": "tap", "ok": False, "reason": "y"},
                        ],
                    }
                ],
            ),
        ]
    )
    assert stats.failing_scenarios[0].run_ids == ("20260101-000000", "20260102-000000")
    # The two failing `checkout > tap` steps live in one run, so its id appears once.
    assert stats.failing_steps[0].key == "checkout > tap"
    assert stats.failing_steps[0].run_ids == ("20260101-000000",)


def test_hotspot_run_ids_empty_when_manifest_has_no_run_id() -> None:
    # A manifest with no runId still counts toward the tally but links to nothing (no dead deep link).
    stats = _stats.aggregate_runs(
        [{"ok": False, "scenarios": [{"scenario": "s", "ok": False, "failure": "boom"}]}]
    )
    assert stats.failing_scenarios[0].failures == 1
    assert stats.failing_scenarios[0].run_ids == ()


def test_render_html_emits_drilldown_deep_links() -> None:
    # BE-0241: in the live serve view, day / backend / hotspot cells link to the SPA's History tab,
    # filtered to their runs.
    html = _stats.render_html(
        _stats.aggregate_runs(
            [
                _manifest(
                    "20260101-000000",
                    ok=False,
                    backend="xcuitest",
                    scenarios=[{"scenario": "checkout", "ok": False, "failure": "timeout"}],
                )
            ]
        ),
        live=True,
    )
    # Hotspot, day, and backend rows all carry the deep link to the matching run.
    assert 'href="/?tab=history&amp;runs=20260101-000000&amp;label=checkout"' in html
    assert "runs=20260101-000000&amp;label=day%202026-01-01" in html
    assert "runs=20260101-000000&amp;label=backend%20xcuitest" in html
    # Still self-contained — the links are plain anchors, no script.
    assert "<script" not in html


def test_render_html_deep_link_repeats_runs_param_per_id() -> None:
    # BE-0241: multiple ids are emitted as repeated `runs=<id>` params (not a comma-joined value),
    # so the SPA's URLSearchParams.getAll('runs') recovers each id even if one contains a comma.
    html = _stats.render_html(
        _stats.aggregate_runs(
            [
                _manifest(
                    "20260101-000000",
                    ok=False,
                    scenarios=[{"scenario": "checkout", "ok": False, "failure": "timeout"}],
                ),
                _manifest(
                    "20260101-120000",
                    ok=False,
                    scenarios=[{"scenario": "checkout", "ok": False, "failure": "timeout"}],
                ),
            ]
        ),
        live=True,
    )
    # The checkout hotspot spans both runs → one runs= param each, no comma delimiter.
    assert (
        'href="/?tab=history&amp;runs=20260101-000000&amp;runs=20260101-120000&amp;label=checkout"'
        in html
    )
    assert "runs=20260101-000000,20260101-120000" not in html


def test_render_html_standalone_export_has_no_drilldown_links() -> None:
    # BE-0241: the default (CLI `stats --html`) export stays self-contained — an absolute
    # `/?tab=history…` link is dead opened from disk, so the cells render as plain text, not anchors.
    html = _stats.render_html(
        _stats.aggregate_runs(
            [
                _manifest(
                    "20260101-000000",
                    ok=False,
                    backend="xcuitest",
                    scenarios=[{"scenario": "checkout", "ok": False, "failure": "timeout"}],
                )
            ]
        )
    )
    assert "tab=history" not in html
    assert "runs=" not in html
    assert 'class="drill"' not in html
    # The underlying data still renders — just without the links.
    assert "checkout" in html and "xcuitest" in html


def test_render_html_no_link_when_run_id_absent() -> None:
    # A hotspot with no contributing run id renders as plain text, never a dead `runs=` link.
    html = _stats.render_html(
        _stats.aggregate_runs([{"ok": False, "scenarios": [{"scenario": "orphan", "ok": False}]}])
    )
    assert "orphan" in html
    assert "runs=" not in html


def test_render_text_summarizes_headline() -> None:
    text = _stats.render(
        _stats.aggregate_runs(
            [
                _manifest("20260101-000000", ok=True, backend="xcuitest"),
                _manifest("20260102-000000", ok=False, backend="xcuitest"),
            ]
        )
    )
    assert "runs: 2" in text
    assert "50%" in text


def test_render_text_empty() -> None:
    assert _stats.render(_stats.aggregate_runs([])) == "no runs to aggregate"


def test_render_html_is_self_contained() -> None:
    html = _stats.render_html(
        _stats.aggregate_runs(
            [
                _manifest(
                    "20260101-000000",
                    scenario_hash="sha256:aaa",
                    scenarios=[{"scenario": "login", "ok": True, "duration_s": 2.0}],
                )
            ]
        )
    )
    assert html.startswith("<!DOCTYPE html>")
    assert "Run stats" in html
    assert "login" in html
    # No external assets / JS: the page must work opened straight from disk.
    assert "<script" not in html
    assert "http://" not in html and "https://" not in html


def test_render_html_escapes_scenario_names() -> None:
    html = _stats.render_html(
        _stats.aggregate_runs(
            [
                _manifest(
                    "20260101-000000",
                    scenario_hash="sha256:aaa",
                    scenarios=[{"scenario": "<img src=x>", "ok": False, "failure": "boom"}],
                )
            ]
        )
    )
    assert "<img src=x>" not in html
    assert "&lt;img" in html


def test_render_text_includes_flaky_scenarios() -> None:
    text = _stats.render(
        _stats.aggregate_runs(
            [
                _manifest(
                    "20260101-000000",
                    scenario_hash="sha256:aaa",
                    scenarios=[{"scenario": "login", "ok": True, "duration_s": 1.0}],
                ),
                _manifest(
                    "20260102-000000",
                    scenario_hash="sha256:aaa",
                    scenarios=[{"scenario": "login", "ok": False, "duration_s": 1.0}],
                ),
            ]
        )
    )
    assert "flaky scenarios:" in text
    assert "login" in text
    assert "1/2 passed" in text


def test_render_html_empty_state() -> None:
    # A fresh runs directory aggregates to zero runs; the HTML shows an empty-state message rather
    # than a meaningless "0/0 runs passed" pass-rate, and still renders cleanly (no Jinja error).
    html = _stats.render_html(_stats.aggregate_runs([]))
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert "No runs to aggregate" in html
    assert "0/0 runs passed" not in html
    assert "<script" not in html


def test_render_text_reports_unknown_backend() -> None:
    stats = _stats.aggregate_runs([_manifest("20260101-000000", ok=True)])  # no backend recorded
    assert "(unknown)=1" in _stats.render(stats)


def test_failing_steps_fall_back_to_question_marks() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenarios=[{"ok": False, "steps": [{"index": 0, "ok": False}]}],
            )
        ]
    )
    assert stats.failing_steps[0].key == "? > ?"


def test_non_mapping_scenario_entries_are_skipped() -> None:
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                ok=False,
                scenario_hash="sha256:aaa",
                scenarios=["not a scenario", {"scenario": "s", "ok": False}],  # type: ignore[list-item]
            )
        ]
    )
    assert stats.runs == 1
    assert [sc.name for sc in stats.scenarios] == ["s"]
    assert [h.key for h in stats.failing_scenarios] == ["s"]


def test_non_numeric_duration_is_excluded_from_average() -> None:
    # A null/missing duration_s must not count as a 0.0 sample that drags the average down.
    stats = _stats.aggregate_runs(
        [
            _manifest(
                "20260101-000000",
                scenario_hash="sha256:aaa",
                scenarios=[{"scenario": "s", "ok": True, "duration_s": 2.0}],
            ),
            _manifest(
                "20260102-000000",
                scenario_hash="sha256:aaa",
                scenarios=[{"scenario": "s", "ok": True, "duration_s": None}],
            ),
        ]
    )
    assert stats.scenarios[0].avg_duration_s == 2.0
    assert stats.scenarios[0].max_duration_s == 2.0


# --- BE-0226 Unit 1: per-project roll-up over the same aggregation ---


def test_project_metrics_rolls_up_headline_numbers() -> None:
    m = _stats.project_metrics(
        "proj-1",
        "checkout",
        [
            _manifest("20260101-000000", ok=True),
            _manifest("20260102-000000", ok=False),
            _manifest("20260103-000000", ok=True),
            _manifest("20260104-000000", ok=True),
        ],
    )
    assert m.project_id == "proj-1"
    assert m.name == "checkout"
    assert m.runs == 4
    assert m.pass_rate == 0.75  # reuses Stats.pass_rate over the window


def test_project_metrics_flaky_rate_is_share_of_flaky_scenarios() -> None:
    # One scenario flips its verdict at a constant fingerprint (flaky); the other is stable.
    m = _stats.project_metrics(
        "p",
        "app",
        [
            _manifest(
                "20260101-000000",
                scenario_hash="sha256:flaky",
                scenarios=[{"scenario": "a", "ok": True}],
            ),
            _manifest(
                "20260102-000000",
                scenario_hash="sha256:flaky",
                scenarios=[{"scenario": "a", "ok": False}],
            ),
            _manifest(
                "20260103-000000",
                scenario_hash="sha256:stable",
                scenarios=[{"scenario": "b", "ok": True}],
            ),
            _manifest(
                "20260104-000000",
                scenario_hash="sha256:stable",
                scenarios=[{"scenario": "b", "ok": True}],
            ),
        ],
    )
    # 1 flaky-classified scenario out of 2 fingerprinted scenarios.
    assert m.flaky_rate == 0.5


def test_project_metrics_duration_percentiles_over_runs() -> None:
    m = _stats.project_metrics(
        "p",
        "app",
        [
            _manifest(
                f"2026010{i}-000000",
                ok=True,
                scenarios=[{"scenario": "s", "ok": True, "duration_s": float(d)}],
            )
            for i, d in enumerate([1.0, 2.0, 3.0, 4.0, 5.0], start=1)
        ],
    )
    assert m.duration_p50_s == 3.0
    assert m.duration_p95_s == 4.8  # linear interpolation: 4.0 + 0.8*(5.0-4.0)


def test_project_metrics_trend_is_daily_pass_rate_oldest_first() -> None:
    m = _stats.project_metrics(
        "p",
        "app",
        [
            _manifest("20260101-000000", ok=True),
            _manifest("20260102-000000", ok=False),
        ],
    )
    assert [(d.day, d.pass_rate) for d in m.trend] == [
        ("2026-01-01", 1.0),
        ("2026-01-02", 0.0),
    ]


def test_project_metrics_empty_run_set_degrades_to_zeros() -> None:
    m = _stats.project_metrics("empty", "unrun", [])
    assert m.runs == 0
    assert m.pass_rate == 0.0
    assert m.flaky_rate == 0.0
    assert m.duration_p50_s == 0.0
    assert m.duration_p95_s == 0.0
    assert m.trend == []

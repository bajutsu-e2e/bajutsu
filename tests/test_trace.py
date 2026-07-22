"""`bajutsu trace` — the text timeline over a finished run."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu import trace


def _write_run(runs: Path, run_id: str, *, ok: bool = True) -> Path:
    run = runs / run_id
    sid = "00-s"
    (run / sid).mkdir(parents=True)
    manifest = {
        "runId": run_id,
        "ok": ok,
        "backend": "xcuitest",
        "scenarios": [
            {
                "scenario": "s",
                "ok": ok,
                "backend": "xcuitest",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "reason": "",
                        "duration_s": 0.3,
                        "started_at": 0.0,
                    },
                    {
                        "index": 1,
                        "action": "wait",
                        "ok": ok,
                        "reason": "" if ok else "timeout",
                        "duration_s": 0.1,
                        "started_at": 0.7,
                    },
                ],
                "expect_results": [
                    {
                        "ok": True,
                        "kind": "request",
                        "detail": "request GET status=200",
                        "reason": "",
                    }
                ],
                "failure": None if ok else "expect: no match",
                "artifacts": [
                    {"name": f"{sid}/network.json", "kind": "network", "provider": "collector"},
                    {"name": f"{sid}/appTrace.json", "kind": "appTrace", "provider": "simctl"},
                ],
            }
        ],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / sid / "network.json").write_text(
        json.dumps(
            [
                {
                    "method": "GET",
                    "url": "https://example.com",
                    "status": 200,
                    "durationMs": 150.0,
                    "startedAt": 0.4,
                },
            ]
        ),
        encoding="utf-8",
    )
    (run / sid / "appTrace.json").write_text(
        json.dumps([{"name": "reindex", "durationMs": 1282.3}]), encoding="utf-8"
    )
    return run


def test_trace_run_renders_timeline(tmp_path: Path) -> None:
    out = trace.trace_run(_write_run(tmp_path / "runs", "20260101-000000"))
    assert "bajutsu trace · run 20260101-000000 · PASS · driver: xcuitest" in out
    assert "▸ s   PASS   [xcuitest]" in out
    # Chronological interleave: tap (0.0s) → net (0.4s) → wait (0.7s).
    assert out.index("✓ tap") < out.index("net  GET") < out.index("✓ wait")
    assert "https://example.com → 200" in out
    assert "✓ request" in out
    assert "reindex   1282.3ms" in out
    assert "evidence: appTrace · network" in out


def test_trace_failure_shows_reason(tmp_path: Path) -> None:
    out = trace.trace_run(_write_run(tmp_path / "runs", "20260101-000001", ok=False))
    assert "FAIL" in out and "✗ wait" in out and "timeout" in out
    assert "failure: expect:" in out


def test_latest_run_picks_newest(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_run(runs, "20260101-000000")
    _write_run(runs, "20260102-000000")
    newest = trace.latest_run(runs)
    assert newest is not None and newest.name == "20260102-000000"
    assert trace.latest_run(tmp_path / "empty") is None


def test_scenario_filter(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", "20260101-000000")
    assert "▸ s" in trace.trace_run(run, "s")
    assert "▸ s" not in trace.trace_run(run, "other")


def _add_scenario_yaml(run: Path, *, step0_from: str, step1_from: str) -> None:
    # scenario "s" with two steps (matching the manifest's tap/wait), carrying `from:` provenance.
    run.joinpath("scenario.yaml").write_text(
        "- name: s\n"
        f"  steps:\n"
        f"    - tap: {{ id: a }}\n      from: {step0_from!r}\n"
        f"    - wait: {{ until: screenChanged, timeout: 5 }}\n      from: {step1_from!r}\n",
        encoding="utf-8",
    )


def test_trace_shows_from_provenance(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", "20260101-000000")
    _add_scenario_yaml(run, step0_from="Open settings", step1_from="Wait for the screen")
    out = trace.trace_run(run)
    # each step carries its originating phrase inline on the timeline
    assert "Open settings" in out and "Wait for the screen" in out
    # the phrase sits on its step's line, after the action
    tap_line = next(line for line in out.splitlines() if "✓ tap" in line)
    assert "Open settings" in tap_line


def test_trace_groups_consecutive_equal_from(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", "20260101-000000")
    _add_scenario_yaml(run, step0_from="Open settings", step1_from="Open settings")
    out = trace.trace_run(run)
    # one utterance produced both steps — the shared phrase is labeled once, not repeated
    assert out.count("Open settings") == 1


def test_trace_groups_from_over_the_full_plan_not_only_executed_steps(tmp_path: Path) -> None:
    # Plan is [A, B, A]; only steps 0 and 2 ran. Grouping is over the *whole plan*, so step 1 (B)
    # breaks the run and both A steps keep their label — matching how the report groups. (Grouping
    # over executed steps alone would wrongly collapse the two A's into one.)
    run = tmp_path / "runs" / "20260101-000000"
    (run / "00-s").mkdir(parents=True)
    run.joinpath("manifest.json").write_text(
        json.dumps(
            {
                "runId": "20260101-000000",
                "ok": True,
                "backend": "xcuitest",
                "scenarios": [
                    {
                        "scenario": "s",
                        "ok": True,
                        "backend": "xcuitest",
                        "steps": [
                            {"index": 0, "action": "tap", "ok": True, "started_at": 0.0},
                            {"index": 2, "action": "tap", "ok": True, "started_at": 0.5},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run.joinpath("scenario.yaml").write_text(
        "- name: s\n"
        "  steps:\n"
        "    - tap: { id: a }\n      from: 'Sign in'\n"
        "    - tap: { id: b }\n      from: 'Dismiss the banner'\n"
        "    - tap: { id: c }\n      from: 'Sign in'\n",
        encoding="utf-8",
    )
    out = trace.trace_run(run)
    assert out.count("Sign in") == 2  # the two non-consecutive (plan-wise) A steps each keep it


def test_trace_omits_provenance_for_control_flow_scenarios(tmp_path: Path) -> None:
    # The manifest indexes steps over a flat counter that also counts steps nested in if/forEach,
    # while our `from:` list is top-level only — so the index spaces diverge. Rather than pair a
    # step's action with another step's phrase, a scenario using control flow shows no provenance.
    run = _write_run(tmp_path / "runs", "20260101-000000")
    run.joinpath("scenario.yaml").write_text(
        "- name: s\n"
        "  steps:\n"
        "    - tap: { id: a }\n      from: 'Open it'\n"
        "    - forEach: { sel: { idMatches: 'row-*' }, as: r, steps: [ { tap: { id: x } } ] }\n"
        "      from: 'For each row'\n",
        encoding="utf-8",
    )
    out = trace.trace_run(run)
    assert "Open it" not in out and "For each row" not in out  # omitted, not mislabeled
    assert "✓ tap" in out  # the timeline itself still renders


def test_trace_without_scenario_yaml_still_renders(tmp_path: Path) -> None:
    # an older run (no scenario.yaml) has no provenance to show, but the timeline still renders
    out = trace.trace_run(_write_run(tmp_path / "runs", "20260101-000000"))
    assert "✓ tap" in out and "✓ wait" in out


def test_step_index_missing_or_invalid_is_a_non_matching_sentinel() -> None:
    # A step with a usable index keeps it; a missing/invalid one returns a negative sentinel so the
    # caller's bounds check omits provenance rather than mislabeling it with step 0's phrase.
    assert trace._step_index({"index": 3}) == 3
    assert trace._step_index({"index": 0}) == 0
    assert trace._step_index({}) < 0
    assert trace._step_index({"index": "x"}) < 0
    assert trace._step_index({"index": True}) < 0  # bool is not a step index


# --- `bajutsu trace` CLI command (BE-0117) ---

from typer.testing import CliRunner  # noqa: E402

from bajutsu.cli import app  # noqa: E402

runner = CliRunner()


def test_cli_trace_renders_run(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", "20260101-000000")
    r = runner.invoke(app, ["trace", str(run)])
    assert r.exit_code == 0
    assert "s" in r.output


def test_cli_trace_no_run_at_explicit_path(tmp_path: Path) -> None:
    empty = tmp_path / "run-without-manifest"
    empty.mkdir()
    r = runner.invoke(app, ["trace", str(empty)])
    assert r.exit_code == 2
    assert "no run found" in r.output
    assert str(empty) in r.output


def test_cli_trace_no_run_under_runs_root(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    r = runner.invoke(app, ["trace", "--runs", str(runs)])
    assert r.exit_code == 2
    assert "no run found" in r.output
    assert "runs" in r.output


def test_cli_trace_explain_needs_scenario_path() -> None:
    r = runner.invoke(app, ["trace", "--explain"])
    assert r.exit_code == 2
    assert "--explain needs a scenario file path" in r.output


def test_cli_trace_explain_missing_file(tmp_path: Path) -> None:
    r = runner.invoke(app, ["trace", "--explain", str(tmp_path / "nope.yaml")])
    assert r.exit_code == 2
    assert "--explain needs a scenario file path" in r.output


def test_cli_trace_explain_invalid_scenario(tmp_path: Path) -> None:
    # Valid YAML whose content is not a valid scenario (missing `name`): the loader raises a
    # ValidationError (a ValueError), which _explain turns into a clean exit 2.
    bad = tmp_path / "bad.yaml"
    bad.write_text("- steps:\n    - tap: { id: ok }\n", encoding="utf-8")
    r = runner.invoke(app, ["trace", "--explain", str(bad)])
    assert r.exit_code == 2
    assert "failed to load scenario" in r.output


def test_cli_trace_explain_malformed_yaml(tmp_path: Path) -> None:
    # YAML that does not parse at all (unclosed flow mapping): the loader normalizes the parse's
    # yaml.YAMLError into a ValueError, so _explain's guard turns it into the same clean exit 2 as a
    # structurally-invalid scenario — not an uncaught traceback with exit 1 (BE-0150).
    bad = tmp_path / "broken.yaml"
    bad.write_text("- name: a\n  steps: { id\n", encoding="utf-8")
    r = runner.invoke(app, ["trace", "--explain", str(bad)])
    assert r.exit_code == 2
    assert "failed to load scenario" in r.output


def test_cli_trace_explain_malformed_component_names_the_component(tmp_path: Path) -> None:
    # A syntax error in a *referenced component* is attributed to the component file, not the
    # top-level scenario: the loader names each file it parses, so it can't misreport which one is
    # malformed (BE-0150).
    (tmp_path / "comp.yaml").write_text("steps: { tap\n", encoding="utf-8")  # unclosed flow mapping
    scenario = tmp_path / "s.yaml"
    scenario.write_text(
        "- name: a\n  steps:\n    - use: { component: comp.yaml }\n", encoding="utf-8"
    )
    r = runner.invoke(app, ["trace", "--explain", str(scenario)])
    assert r.exit_code == 2
    assert "failed to load scenario" in r.output
    assert "comp.yaml" in r.output  # the component, not the scenario, is named as malformed


def test_cli_trace_explain_renders_valid_scenario(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text("- name: a\n  steps:\n    - tap: { id: ok }\n", encoding="utf-8")
    r = runner.invoke(app, ["trace", "--explain", str(good)])
    assert r.exit_code == 0

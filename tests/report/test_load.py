"""Tests for loading a persisted run back into the report renderer (BE-0068).

The renderer must be a pure function of data stored in the run dir, so a finished run can be
re-rendered offline. These pin the round-trip (manifest -> RunResults without loss), version
tolerance (an older manifest still loads), and that the manifest carries the render model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bajutsu.assertions import AssertionResult, VisualEvidence
from bajutsu.evidence import Artifact
from bajutsu.orchestrator import AlertEvent, RunResult, SkippedCapture, StepOutcome
from bajutsu.report.load import load_run, results_from_manifest
from bajutsu.report.manifest import manifest_dict


def _result() -> RunResult:
    return RunResult(
        scenario="checkout",
        ok=False,
        steps=[
            StepOutcome(
                index=0,
                action="tap home.start",
                ok=True,
                duration_s=0.5,
                started_at=0.0,
                assertion_results=[AssertionResult(ok=True, kind="exists", detail="home.title")],
                artifacts=[Artifact("0/shot.png", "screenshot", "simctl")],
                alerts=[AlertEvent("Allow")],
            ),
            StepOutcome(index=1, action="tap pay", ok=False, reason="not found"),
        ],
        expect_results=[
            AssertionResult(
                ok=False,
                kind="visual",
                detail="home",
                reason="diff 3%",
                visual=VisualEvidence("home", "1/visual-actual.png", diff_pct=3.0, missing=False),
            )
        ],
        failure="pay missing",
        artifacts=[Artifact("network.json", "network", "collector")],
        backend="xcuitest",
        device="UDID-1",
        device_name="iPhone 17 Pro",
        device_runtime="iOS 26",
        duration_s=2.5,
        expect_alerts=[AlertEvent("Dismiss")],
        skipped_captures=[SkippedCapture("video", "no eligible backend")],
    )


def test_round_trip_through_manifest_is_lossless() -> None:
    original = [_result()]
    # go through JSON the way the run dir does, then back
    data = json.loads(json.dumps(manifest_dict("r1", original)))
    assert results_from_manifest(data) == original


def test_manifest_carries_schema_version_and_source_name() -> None:
    data = manifest_dict("r1", [_result()], source_name="smoke.yaml")
    assert (
        data["schemaVersion"] == 4
    )  # bumped for the optional cross-browser matrix block (BE-0076)
    assert data["sourceName"] == "smoke.yaml"


def test_loads_a_legacy_manifest_without_schema_version() -> None:
    # a run baked before versioning: no schemaVersion / sourceName, no newer fields
    legacy = {
        "runId": "old",
        "ok": True,
        "backend": "xcuitest",
        "scenarios": [{"scenario": "smoke", "ok": True, "steps": []}],
    }
    [r] = results_from_manifest(legacy)
    assert r.scenario == "smoke" and r.ok is True and r.steps == []


def test_ignores_unknown_newer_fields() -> None:
    # a manifest from a newer version with a field this code doesn't know must not crash
    data = manifest_dict("r1", [_result()])
    data["scenarios"][0]["futureField"] = "ignored"  # type: ignore[index]
    assert results_from_manifest(data)[0].scenario == "checkout"


def test_load_run_normalizes_malformed_scenario_to_valueerror(tmp_path: Path) -> None:
    # A manifest present but a corrupt scenario.yaml is "malformed" — load_run raises ValueError
    # (not a bare yaml.YAMLError), honoring its one documented malformed-input type so callers can
    # catch a single type for "can't load this run" (BE-0068 serve render-on-view falls back then).
    run = tmp_path / "r1"
    run.mkdir()
    (run / "manifest.json").write_text('{"runId": "r1", "scenarios": []}', encoding="utf-8")
    (run / "scenario.yaml").write_text("{ bad: yaml ::", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed run model"):
        load_run(run)


def test_load_run_missing_file_raises_oserror(tmp_path: Path) -> None:
    # A missing run (no manifest.json) stays an OSError, distinct from malformed content.
    with pytest.raises(OSError, match="manifest"):
        load_run(tmp_path / "nope")

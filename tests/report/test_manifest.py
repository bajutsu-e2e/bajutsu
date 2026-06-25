"""Tests for the report manifest.json and JUnit XML."""

from __future__ import annotations

import json
from pathlib import Path

from _report import _failing, _passing

from bajutsu.idb_version import IdbVersions
from bajutsu.orchestrator import AlertEvent, RunResult, StepOutcome
from bajutsu.report import junit_xml, manifest_dict, write_report


def test_manifest_structure() -> None:
    m = manifest_dict("run1", [_passing()])
    assert m["runId"] == "run1"
    assert m["ok"] is True
    scenarios = m["scenarios"]
    assert isinstance(scenarios, list)
    assert scenarios[0]["scenario"] == "s1"
    assert scenarios[0]["ok"] is True
    assert scenarios[0]["steps"][0]["action"] == "tap"


def test_manifest_overall_ok_is_and() -> None:
    assert manifest_dict("r", [_passing(), _failing()])["ok"] is False


def test_manifest_records_backend() -> None:
    # run_scenario stamps each result with the driver it ran (here the fake driver),
    # and the manifest summarizes the run's actuator at top level.
    m = manifest_dict("run1", [_passing()])
    assert m["backend"] == "fake"
    assert m["scenarios"][0]["backend"] == "fake"


def test_manifest_records_idb_versions_as_provenance() -> None:
    # The idb versions a run was driven against are recorded so any artifact set states exactly
    # which idb produced it — provenance, never affecting ok/pass-fail (BE-0005).
    m = manifest_dict(
        "run1", [_passing()], idb_versions=IdbVersions(companion="1.1.8", client="1.2")
    )
    assert m["idb"] == {"companion": "1.1.8", "client": "1.2"}
    assert m["ok"] is True  # provenance does not change the verdict


def test_manifest_omits_idb_versions_when_not_probed() -> None:
    # A non-idb backend (or a host without idb) records nothing rather than a misleading null block.
    assert "idb" not in manifest_dict("run1", [_passing()])


# --- run provenance & version stamping (BE-0049, the longitudinal-flakiness prerequisite) ---


def test_run_provenance_hashes_the_scenario_deterministically() -> None:
    from bajutsu.report.manifest import run_provenance

    yaml = "- name: s\n  steps:\n    - tap: { id: a }\n"
    p1 = run_provenance(yaml, git_revision=None)
    p2 = run_provenance(yaml, git_revision=None)
    assert p1["scenarioHash"] == p2["scenarioHash"]  # same content → same fingerprint
    assert p1["scenarioHash"].startswith("sha256:")
    # a different scenario fingerprints differently
    assert run_provenance(yaml + "\n", git_revision=None)["scenarioHash"] != p1["scenarioHash"]


def test_run_provenance_records_the_tool_version() -> None:
    from bajutsu import __version__
    from bajutsu.report.manifest import run_provenance

    assert run_provenance("x", git_revision=None)["toolVersion"] == __version__


def test_run_provenance_includes_git_revision_only_when_known() -> None:
    from bajutsu.report.manifest import run_provenance

    assert run_provenance("x", git_revision="abc123")["gitRevision"] == "abc123"
    # an unresolvable revision (not a git checkout) omits the key rather than recording null
    assert "gitRevision" not in run_provenance("x", git_revision=None)


def test_manifest_records_provenance_block() -> None:
    from bajutsu.report.manifest import run_provenance

    prov = run_provenance("- name: s\n  steps: []\n", git_revision="deadbeef")
    m = manifest_dict("run1", [_passing()], provenance=prov)
    assert m["provenance"] == prov
    assert m["ok"] is True  # provenance is metadata; it never changes the verdict


def test_manifest_omits_provenance_when_absent() -> None:
    assert "provenance" not in manifest_dict("run1", [_passing()])


def test_manifest_omits_idb_block_when_both_versions_unknown() -> None:
    # A {companion: null, client: null} block carries no provenance — omit it, don't add noise.
    m = manifest_dict("run1", [_passing()], idb_versions=IdbVersions(companion=None, client=None))
    assert "idb" not in m


def test_junit_pass_and_fail() -> None:
    ok_xml = junit_xml([_passing()])
    assert 'tests="1"' in ok_xml
    assert 'failures="0"' in ok_xml
    assert "<failure" not in ok_xml

    bad_xml = junit_xml([_failing()])
    assert 'failures="1"' in bad_xml
    assert "<failure" in bad_xml


def test_write_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run3"
    manifest_path = write_report(run_dir, "run3", [_passing(), _failing()])
    assert manifest_path.exists()
    assert (run_dir / "junit.xml").exists()
    assert (run_dir / "report.html").exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["runId"] == "run3"
    assert data["ok"] is False
    assert len(data["scenarios"]) == 2


def test_manifest_records_scenario_duration() -> None:
    r = RunResult(scenario="s1", ok=True, steps=[], duration_s=2.5)
    assert manifest_dict("run1", [r])["scenarios"][0]["duration_s"] == 2.5


def test_manifest_records_device_environment() -> None:
    r = _passing()
    r.device, r.device_name, r.device_runtime = "SIM-1", "iPhone 15", "iOS 17.2"
    scenario = manifest_dict("run1", [r])["scenarios"][0]
    assert scenario["device"] == "SIM-1"
    assert scenario["device_name"] == "iPhone 15"
    assert scenario["device_runtime"] == "iOS 17.2"


def test_manifest_records_dismissed_alerts() -> None:
    # asdict captures the dismissals so the manifest (the source of truth) carries them too.
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[StepOutcome(index=0, action="tap", ok=True, alerts=[AlertEvent(label="Not Now")])],
        expect_alerts=[AlertEvent(label="Allow")],
    )
    m = manifest_dict("run1", [r])
    scenario = m["scenarios"][0]
    assert scenario["steps"][0]["alerts"] == [{"label": "Not Now"}]
    assert scenario["expect_alerts"] == [{"label": "Allow"}]

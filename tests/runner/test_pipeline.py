"""Tests for run_all / run_and_report (scenarios + leases -> results + report artifacts)."""

from __future__ import annotations

import json
from pathlib import Path

from _runner import _eff, _el, _fake_driver, _lease

from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import NullSink
from bajutsu.runner import (
    Lease,
    run_all,
    run_and_report,
)
from bajutsu.scenario import Scenario


def test_run_all() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "missing"}}]}),
    ]
    results = run_all(_eff(), scenarios, _lease)
    assert [r.ok for r in results] == [True, False]


def test_preflight_fails_unsupported_scenario_before_leasing() -> None:
    # A pinch needs multiTouch, which idb lacks — the preflight fails the scenario up front, so the
    # lease (device work) is never reached (BE-0082).
    scenarios = [
        Scenario.model_validate(
            {"name": "z", "steps": [{"pinch": {"sel": {"id": "m"}, "scale": 2.0}}]}
        )
    ]

    def lease_must_not_run(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("lease must not be called when the preflight rejects the scenario")

    results = run_all(_eff(), scenarios, lease_must_not_run, actuator="idb")
    assert len(results) == 1 and not results[0].ok
    assert results[0].backend == "idb"
    assert "multiTouch" in (results[0].failure or "")


def test_preflight_allows_supported_scenario_on_idb() -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    results = run_all(_eff(), scenarios, _lease, actuator="idb")
    assert results[0].ok


def test_run_all_parallel_preserves_order_and_releases() -> None:
    scenarios = [
        Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]})
        for n in ("a", "b", "c")
    ]
    released: list[str] = []

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: released.append(s.name),
        )

    results = run_all(_eff(), scenarios, lease, workers=2)
    assert [r.scenario for r in results] == ["a", "b", "c"]  # order preserved despite concurrency
    assert all(r.ok for r in results)
    assert len(released) == 3 and set(released) == {"a", "b", "c"}  # every leased device released


def test_run_all_releases_after_each_scenario() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "ok"}}]}),
    ]
    released: list[str] = []

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: released.append(s.name),
        )

    run_all(_eff(), scenarios, lease)
    assert released == ["a", "b"]  # release runs after every scenario, including the last


def test_run_all_on_blocked_for_selects_per_scenario() -> None:
    # The factory picks each scenario's guard from its dismissAlerts: the guarded scenario
    # recovers from a blocked tap and passes; the one that disabled it fails.
    from bajutsu.orchestrator import AlertEvent, BlockedHandler

    scenarios = [
        Scenario.model_validate(
            {"name": "guarded", "dismissAlerts": True, "steps": [{"tap": {"id": "later"}}]}
        ),
        Scenario.model_validate(
            {"name": "bare", "dismissAlerts": False, "steps": [{"tap": {"id": "later"}}]}
        ),
    ]

    def recover(d: base.Driver) -> AlertEvent:
        assert isinstance(d, FakeDriver)
        d.screen = [_el("later", "Later", ["button"])]  # "dismiss the alert": target appears
        return AlertEvent(label="x")

    def on_blocked_for(s: Scenario) -> BlockedHandler | None:
        cfg = s.dismiss_alerts
        return None if cfg is not None and not cfg.enabled else recover

    results = run_all(_eff(), scenarios, _lease, on_blocked_for=on_blocked_for)
    assert [r.ok for r in results] == [True, False]


def test_run_all_attributes_each_scenario_to_its_device() -> None:
    scenarios = [
        Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]}) for n in ("a", "b")
    ]

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            udid=f"DEV-{s.name}",
        )

    results = run_all(_eff(), scenarios, lease, workers=2)
    # Each result records the device that ran it, so the report can show the parallel split.
    assert {r.scenario: r.device for r in results} == {"a": "DEV-a", "b": "DEV-b"}


def test_run_and_report(tmp_path: Path) -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    results, manifest = run_and_report(_eff(), scenarios, _lease, tmp_path / "runs", "run1")
    assert results[0].ok
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["runId"] == "run1"
    assert (tmp_path / "runs" / "run1" / "junit.xml").exists()
    # The executed scenario is kept alongside its results.
    scn_file = tmp_path / "runs" / "run1" / "scenario.yaml"
    assert scn_file.exists() and "name: a" in scn_file.read_text(encoding="utf-8")
    # The run is stamped with provenance (BE-0049): a fingerprint of the executed scenario YAML
    # (taken pre-redaction) plus the tool version, so accumulated runs group by identity. With no
    # secret_values here nothing is scrubbed, so the stamp also equals a hash of the saved file.
    import hashlib

    from bajutsu import __version__

    prov = data["provenance"]
    expected = "sha256:" + hashlib.sha256(scn_file.read_text(encoding="utf-8").encode()).hexdigest()
    assert prov["scenarioHash"] == expected
    assert prov["toolVersion"] == __version__
    assert "configSource" not in prov  # a local config records no Git source


def test_run_and_report_records_git_config_source(tmp_path: Path) -> None:
    # A run from a Git config source stamps which repo@sha it executed into the manifest (BE-0063).
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    src = {"host": "github.com", "owner": "acme", "repo": "tests", "ref": "main", "sha": "deadbeef"}
    _, manifest = run_and_report(
        _eff(), scenarios, _lease, tmp_path / "runs", "run1", config_source=src
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["provenance"]["configSource"] == src


def test_git_revision_maps_failure_and_blank_to_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The subprocess is an external dependency, so it's the one place a stub is warranted. A
    # non-zero exit, a thrown error, and a 0-exit-but-blank stdout (a shimmed `git`) all mean
    # "unknown revision" — None, never an empty stamp.
    import subprocess as sp

    from bajutsu.runner import pipeline

    def fake(result: sp.CompletedProcess[str] | Exception):  # type: ignore[no-untyped-def]
        def run(*a: object, **k: object) -> sp.CompletedProcess[str]:
            if isinstance(result, Exception):
                raise result
            return result

        return run

    monkeypatch.setattr(pipeline.subprocess, "run", fake(sp.CompletedProcess([], 128, "", "fatal")))
    assert pipeline._git_revision() is None  # not a repo
    monkeypatch.setattr(pipeline.subprocess, "run", fake(sp.CompletedProcess([], 0, "   \n", "")))
    assert pipeline._git_revision() is None  # 0 exit but blank stdout → unknown, not ""
    monkeypatch.setattr(pipeline.subprocess, "run", fake(FileNotFoundError("git")))
    assert pipeline._git_revision() is None  # git absent
    monkeypatch.setattr(
        pipeline.subprocess, "run", fake(sp.CompletedProcess([], 0, "abc123\n", ""))
    )
    assert pipeline._git_revision() == "abc123"  # normal: trimmed sha


def test_run_and_report_forwards_baselines_dir(tmp_path: Path) -> None:
    # A visual expect with the baselines dir forwarded builds a VisualContext, so a missing
    # baseline reports "baseline not found" — not "no visual context" (the unforwarded bug).
    scenarios = [
        Scenario.model_validate(
            {
                "name": "vis",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [{"visual": {"baseline": "home.png"}}],
            }
        )
    ]
    results, _ = run_and_report(
        _eff(),
        scenarios,
        _lease,
        tmp_path / "runs",
        "run1",
        baselines_dir=tmp_path / "baselines",
    )
    ev = results[0].expect_results[0]
    assert ev.kind == "visual"
    assert "baseline not found" in ev.reason
    assert ev.visual is not None and ev.visual.missing


def test_run_and_report_forwards_schemas_dir(tmp_path: Path) -> None:
    # A responseSchema expect with schemas_dir forwarded builds a SchemaContext, so the failure
    # gets past the "no schema context" guard (here it fails later, on no matching exchange) —
    # proving the dir was threaded, not dropped.
    scenarios = [
        Scenario.model_validate(
            {
                "name": "rs",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [
                    {"responseSchema": {"request": {"path": "/api/items"}, "schema": "items.json"}}
                ],
            }
        )
    ]
    results, _ = run_and_report(
        _eff(), scenarios, _lease, tmp_path / "runs", "run1", schemas_dir=tmp_path / "schemas"
    )
    ev = results[0].expect_results[0]
    assert ev.kind == "responseSchema"
    assert "no schema context" not in ev.reason  # context was forwarded


def test_run_and_report_scrubs_secret_values_from_artifacts(tmp_path: Path) -> None:
    """The run-level scrub is the final safety net: a secret that reaches result text (here a
    failing assertion's expected value, interpolated from a binding) must not survive into any
    written artifact, even though the scenario definition only ever holds the token."""
    secret = "S3CR3T-TOKEN"
    scenarios = [
        Scenario.model_validate(
            {
                "name": "a",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [{"value": {"sel": {"id": "ok"}, "equals": "${secrets.token}"}}],
            }
        )
    ]
    results, _ = run_and_report(
        _eff(),
        scenarios,
        _lease,
        tmp_path / "runs",
        "run1",
        bindings={"secrets.token": secret},
        secret_values=[secret],
    )
    # The assertion failed, so the secret value really did reach the in-memory result text.
    assert not results[0].ok
    assert results[0].failure is not None and secret in results[0].failure
    # ...but it is scrubbed out of every written artifact.
    run_dir = tmp_path / "runs" / "run1"
    for name in ("manifest.json", "junit.xml", "scenario.yaml"):
        assert secret not in (run_dir / name).read_text(encoding="utf-8")

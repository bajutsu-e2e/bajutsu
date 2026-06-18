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

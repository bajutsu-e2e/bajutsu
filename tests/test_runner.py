"""Tests for the run pipeline (config + scenarios + driver factory -> report)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.runner import device_relauncher, device_teardown, run_all, run_and_report
from bajutsu.scenario import Redact, Relaunch, Scenario


def _eff() -> Effective:
    return Effective(
        app="demo",
        bundle_id="com.example.demo",
        deeplink_scheme=None,
        backend=["fake"],
        device="iPhone 15",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=["screenshot.after"],
        redact=Redact(),
    )


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


# A factory that returns a fake driver whose screen always contains "ok".
def _factory(eff: Effective, scenario: Scenario) -> base.Driver:
    return FakeDriver([_el("ok", "OK", ["button"])])


def test_run_all() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "missing"}}]}),
    ]
    results = run_all(_eff(), scenarios, _factory)
    assert [r.ok for r in results] == [True, False]


def test_run_all_parallel_preserves_order_and_releases() -> None:
    from bajutsu.runner import run_all as _run_all

    scenarios = [
        Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]}) for n in ("a", "b", "c")
    ]
    released: list[base.Driver] = []
    results = _run_all(_eff(), scenarios, _factory, workers=2, release=released.append)
    assert [r.scenario for r in results] == ["a", "b", "c"]  # order preserved despite concurrency
    assert all(r.ok for r in results)
    assert len(released) == 3  # every leased driver is released


def test_run_all_parallel_rejects_shared_collector() -> None:
    from bajutsu.network import NetworkCollector

    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    with pytest.raises(ValueError, match="並列"):
        run_all(_eff(), scenarios, _factory, workers=2, collector=NetworkCollector())


def test_run_all_tears_down_after_each_scenario() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "ok"}}]}),
    ]
    torn: list[str] = []
    run_all(_eff(), scenarios, _factory, teardown=lambda eff, s: torn.append(s.name))
    assert torn == ["a", "b"]  # teardown runs after every scenario, including the last


def test_device_teardown_terminates_the_app() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], env: object = None) -> str:
        calls.append(args)
        return ""

    teardown = device_teardown("UDID-1", env_run=fake_run)
    teardown(_eff(), Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}))
    assert calls == [["xcrun", "simctl", "terminate", "UDID-1", "com.example.demo"]]


def test_relauncher_relaunches_with_locale_and_overrides() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], env: object = None) -> str:
        calls.append(args)
        return ""

    # Scenario locale (ja_JP) overrides the app/config default (en_US from _eff()).
    scn = Scenario.model_validate(
        {"name": "a", "preconditions": {"locale": "ja_JP"}, "steps": [{"tap": {"id": "ok"}}]}
    )
    driver = FakeDriver([_el("home.title", "H"), _el("ok", "OK")])  # 2 elems -> ready immediately
    relaunch = device_relauncher("UDID-1", env_run=fake_run)(_eff(), scn, driver)
    relaunch(Relaunch(env={"K": "V"}, args=["--fresh"]))

    assert ["xcrun", "simctl", "terminate", "UDID-1", "com.example.demo"] in calls
    launch = next(c for c in calls if "launch" in c)
    assert "--fresh" in launch  # per-relaunch arg
    # Locale forced via app launch args, scenario locale winning.
    assert launch[launch.index("-AppleLocale") + 1] == "ja_JP"
    assert "(ja)" in launch


def test_run_and_report(tmp_path: Path) -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    results, manifest = run_and_report(_eff(), scenarios, _factory, tmp_path / "runs", "run1")
    assert results[0].ok
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["runId"] == "run1"
    assert (tmp_path / "runs" / "run1" / "junit.xml").exists()
    # The executed scenario is kept alongside its results.
    scn_file = tmp_path / "runs" / "run1" / "scenario.yaml"
    assert scn_file.exists() and "name: a" in scn_file.read_text(encoding="utf-8")

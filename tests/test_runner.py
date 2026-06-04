"""Tests for the run pipeline (config + scenarios + driver factory -> report)."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.runner import run_all, run_and_report
from bajutsu.scenario import Redact, Scenario


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


def test_run_and_report(tmp_path: Path) -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    results, manifest = run_and_report(_eff(), scenarios, _factory, tmp_path / "runs", "run1")
    assert results[0].ok
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["runId"] == "run1"
    assert (tmp_path / "runs" / "run1" / "junit.xml").exists()

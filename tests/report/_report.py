"""Shared fixtures for the report tests: an element builder and passing/failing runs."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import RunResult, run_scenario
from bajutsu.scenario import Scenario


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _passing() -> RunResult:
    driver = FakeDriver([_el("home.title", "H"), _el("a", "A", ["button"])])
    return run_scenario(
        driver,
        Scenario.model_validate(
            {
                "name": "s1",
                "steps": [{"tap": {"id": "a"}}],
                "expect": [{"exists": {"id": "home.title"}}],
            }
        ),
    )


def _failing() -> RunResult:
    driver = FakeDriver([_el("a", "A", ["button"])])
    return run_scenario(
        driver,
        Scenario.model_validate(
            {
                "name": "s2",
                "steps": [{"tap": {"id": "a"}}],
                "expect": [{"exists": {"id": "missing"}}],
            }
        ),
    )

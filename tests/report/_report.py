"""Shared fixtures for the report tests: an element builder and passing/failing runs."""

from __future__ import annotations

from typing import Any

from bajutsu.common.scenario import Scenario
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import RunResult, run_scenario


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
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


# The report builders return `dict[str, object]` — an honest type for a JSON document, and one an
# assertion cannot index into. These narrow a value out of such a document with a real runtime
# check, so a malformed document fails the test loudly instead of being cast away (BE-0388).


def _scenarios(manifest: dict[str, object]) -> list[Any]:
    """The manifest's per-scenario entries."""
    entries = manifest["scenarios"]
    assert isinstance(entries, list)
    return entries


def _json_obj(value: object) -> dict[str, Any]:
    """One nested object out of a JSON document."""
    assert isinstance(value, dict)
    return value


def _json_str(value: object) -> str:
    """One string value out of a JSON document."""
    assert isinstance(value, str)
    return value

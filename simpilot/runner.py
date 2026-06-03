"""Run pipeline — execute scenarios through a driver factory and write the report.

The driver factory encapsulates "launch the app for this scenario and return a
ready driver", so the runner stays backend-agnostic and testable with the fake
driver.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from simpilot.config import Effective
from simpilot.drivers import base
from simpilot.orchestrator import Clock, RunResult, run_scenario
from simpilot.report import write_report
from simpilot.scenario import Scenario

DriverFactory = Callable[[Effective, Scenario], base.Driver]


def run_all(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    clock: Clock | None = None,
) -> list[RunResult]:
    """Run every scenario, each with a freshly built driver."""
    return [run_scenario(factory(eff, s), s, clock) for s in scenarios]


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
) -> tuple[list[RunResult], Path]:
    """Run scenarios and write manifest.json + JUnit under runs_dir/run_id."""
    results = run_all(eff, scenarios, factory, clock)
    manifest = write_report(runs_dir / run_id, run_id, results)
    return results, manifest

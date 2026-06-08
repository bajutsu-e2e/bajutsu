"""Run pipeline — execute scenarios through a driver factory and write the report.

The driver factory encapsulates "launch the app for this scenario and return a
ready driver", so the runner stays backend-agnostic and testable with the fake
driver.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from bajutsu import env
from bajutsu.backends import default_available, make_driver, select_actuator
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink
from bajutsu.network import NetworkCollector, NetworkExchange
from bajutsu.orchestrator import BlockedHandler, Clock, RunResult, run_scenario, scenario_slug
from bajutsu.redaction import Redactor
from bajutsu.report import write_report
from bajutsu.scenario import Preconditions, Scenario, dump_scenarios, scenario_dict

DriverFactory = Callable[[Effective, Scenario], base.Driver]
# Run after each scenario finishes (e.g. terminate the app -> back to SpringBoard).
Teardown = Callable[[Effective, Scenario], None]


def _no_net() -> list[NetworkExchange]:
    return []


def _write_network(
    exchanges: list[NetworkExchange], run_dir: Path, sid: str, redactor: Redactor
) -> Artifact | None:
    """Write a scenario's observed exchanges to <sid>/network.json (redacted)."""
    if not exchanges:
        return None
    data = [ex.model_dump(by_alias=True, exclude_none=True) for ex in exchanges]
    text = redactor.redact_text(json.dumps(data, ensure_ascii=False, indent=2))
    out = run_dir / sid / "network.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return Artifact(f"{sid}/network.json", "network", "collector")


def launch_driver(
    udid: str,
    eff: Effective,
    actuator: str,
    preconditions: Preconditions | None = None,
    env_run: env.RunFn = env._real_run,
) -> base.Driver:
    """Erase/boot/launch the app (with config + scenario env) and return a driver.

    The simctl sequencing is best-effort and should be confirmed on a real device.
    """
    pre = preconditions or Preconditions()
    e = env.Env(udid, run=env_run)
    if pre.erase:
        e.erase()
    e.boot()
    e.terminate(eff.bundle_id)  # clean start so readiness reflects the new launch
    launch_env: Mapping[str, str] = {**eff.launch_env, **pre.launch_env}
    e.launch(eff.bundle_id, [*eff.launch_args, *pre.launch_args], launch_env)
    if pre.deeplink is not None:
        e.openurl(pre.deeplink)
    driver = make_driver(actuator, udid)
    _await_ready(driver)
    return driver


def _await_ready(driver: base.Driver, timeout: float = 10.0, poll: float = 0.2) -> None:
    """Poll until the launched app has rendered a UI (more than the app root element)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if len(driver.query()) >= 2:
                return
        except Exception:  # noqa: BLE001 — app may not be answerable yet
            pass
        time.sleep(poll)


def run_all(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    sink: EvidenceSink | None = None,
    teardown: Teardown | None = None,
    collector: NetworkCollector | None = None,
    run_dir: Path | None = None,
) -> list[RunResult]:
    """Run every scenario, each with a freshly built driver.

    After each scenario finishes, `teardown` runs (e.g. terminate the app so the
    Simulator returns to SpringBoard between scenarios and after the last one). When a
    `collector` is given, its exchanges are cleared per scenario, exposed to `request`
    assertions, and written to <sid>/network.json.
    """
    redactor = Redactor(eff.redact)
    results: list[RunResult] = []
    for i, s in enumerate(scenarios):
        sid = f"{i:02d}-{scenario_slug(s.name)}"
        if collector is not None:
            collector.clear()
        result = run_scenario(
            factory(eff, s), s, clock, sink=sink, on_blocked=on_blocked,
            scenario_id=sid, network=(collector.snapshot if collector is not None else _no_net),
        )
        if teardown is not None:
            teardown(eff, s)
        if collector is not None and run_dir is not None:
            art = _write_network(collector.snapshot(), run_dir, sid, redactor)
            if art is not None:
                result.artifacts.append(art)
        results.append(result)
    return results


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    sink: EvidenceSink | None = None,
    teardown: Teardown | None = None,
    collector: NetworkCollector | None = None,
) -> tuple[list[RunResult], Path]:
    """Run scenarios and write manifest.json + JUnit under runs_dir/run_id."""
    results = run_all(
        eff, scenarios, factory, clock, on_blocked=on_blocked, sink=sink, teardown=teardown,
        collector=collector, run_dir=runs_dir / run_id,
    )
    # The merged Steps tab renders each scenario as a structured view (definitions)
    # with a toggle to the raw YAML (sources).
    definitions = [scenario_dict(s) for s in scenarios]
    sources = [dump_scenarios([s]) for s in scenarios]
    manifest = write_report(runs_dir / run_id, run_id, results, definitions, sources)
    return results, manifest


def device_factory(
    udid: str,
    backends: list[str],
    available: Callable[[str], bool] = default_available,
    env_run: env.RunFn = env._real_run,
) -> DriverFactory:
    """A real driver factory: pick the actuator, then launch the app per scenario.

    The simctl sequencing (erase/boot) is best-effort and should be confirmed on a
    real device.
    """
    actuator = select_actuator(backends, available)

    def factory(eff: Effective, scenario: Scenario) -> base.Driver:
        return launch_driver(udid, eff, actuator, scenario.preconditions, env_run)

    return factory


def device_teardown(udid: str, env_run: env.RunFn = env._real_run) -> Teardown:
    """A teardown that terminates the app after each scenario, returning the
    Simulator to SpringBoard."""
    e = env.Env(udid, run=env_run)

    def teardown(eff: Effective, scenario: Scenario) -> None:
        e.terminate(eff.bundle_id)

    return teardown

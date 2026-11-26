"""What a run would take from a lease broker: `python -m bajutsu.run.demand [run flags]` (BE-0511).

A worker borrows devices before it starts a run, so it has to know how many. This takes exactly the
flags of the run, loads the config and scenarios the way the run does, and prints one JSON line:
for each platform, how many devices one scenario needs at once (`pool_demand`) and the models the
targets' `runsOn` names. It resolves no device and decides no verdict.

A module run as a process of its own, in the run's directory and environment, so a relative path, a
Git config, and a secret resolve here as they will for the run.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typer

from bajutsu import cli as _cli
from bajutsu.cli._shared import (
    _backends,
    _load_device_worker_or_exit,
    _with_erase_flag,
    _with_worker_device,
    _worker_backends,
)
from bajutsu.common.backends import platform_of, select_actuator
from bajutsu.common.runner.device_provider.lease_command import declared_models
from bajutsu.common.scenario import Scenario
from bajutsu.run import cli as run_cli
from bajutsu.run.internal import PROG_NAME, build_app
from bajutsu.run.pool_demand import pool_demand


def run_demand(args: Sequence[str]) -> dict[str, Any]:
    """The devices a run given *args* would borrow, as ``{"pools": [...], "workers": N}``.

    Raises:
        typer.Exit: The config or the scenarios do not load, as the run itself would report.
    """
    p = typer.main.get_command(build_app()).make_context(PROG_NAME, list(args)).params
    worker_file = p["worker_config"]
    worker = _load_device_worker_or_exit(Path(worker_file) if worker_file else None)
    files = list(p["scenario"] or [])
    explicit = bool(p["target_name"])
    primary = run_cli._resolve_primary_target(p["target_name"], files)

    def load(target: str) -> tuple[Any, Any, list[Scenario]]:
        loaded, eff, _source, _engines = run_cli._resolve_config_and_engines(
            p["config"],
            target,
            offline=p["config_offline"],
            require_pinned=p["require_pinned_config"],
            headed=p["headed"],
            browser=p["browser"],
            browsers=p["browsers"],
            kind=p["kind"],
        )
        scenarios, *_rest = run_cli._load_scenarios(eff, files, target)
        selected = run_cli._filter_scenarios(scenarios, p["tag"], p["exclude"], p["erase"])
        return loaded, eff, selected

    loaded, eff, scenarios = load(primary)
    if not explicit and scenarios and not run_cli._uses_target(scenarios, primary):
        primary = scenarios[0].target_names[0]
        loaded, eff, scenarios = load(primary)
    # A scenario this worker cannot run fails without a device, so it adds no demand (BE-0450).
    failures = run_cli._worker_capability_failures(loaded, scenarios, eff, p["backend"], worker)
    scenarios = [s for i, s in enumerate(scenarios) if i not in failures]
    if not scenarios:
        return {"pools": [], "workers": 1}
    if not run_cli._uses_target(scenarios, primary):
        primary = scenarios[0].target_names[0]
        loaded, eff, _ = load(primary)

    target_effs = {
        name: _with_worker_device(_with_erase_flag(target_eff, p["erase"]), worker)
        for name, target_eff in run_cli._resolve_target_effs(
            loaded, scenarios, primary, eff
        ).items()
    }
    used = list(dict.fromkeys(n for s in scenarios for n in (s.target_names or [primary])))
    actuators = {
        name: select_actuator(
            _worker_backends(_backends(p["backend"], target_effs[name].backend), worker)
        )
        for name in used
    }
    demand = pool_demand(scenarios, actuators)
    # A scenario that declares no target holds one device of the primary's pool.
    if any(not s.target_names for s in scenarios):
        demand.setdefault(actuators[primary], 1)

    pools: dict[str, dict[str, Any]] = {}
    for actuator, count in demand.items():
        platform = platform_of(actuator)
        if platform is None or platform not in (worker.platforms or ()):
            continue
        models = list(
            dict.fromkeys(
                m
                for name in used
                if actuators[name] == actuator
                for m in declared_models(target_effs[name])
            )
        )
        pools[platform] = {"platform": platform, "count": count, "models": models}
    return {"pools": list(pools.values()), "workers": max(1, p["workers"])}


def main() -> None:
    """Print the demand of the run the arguments describe as the last line of standard output."""
    _cli.bootstrap(configure_logging=True)
    try:
        demand = run_demand(sys.argv[1:])
    except typer.Exit as e:
        sys.exit(e.exit_code or 1)
    print(json.dumps(demand), flush=True)


if __name__ == "__main__":
    main()

"""The `relaunch`-step factories for the device and web families (the module's public factory
surface, alongside `environment_for`).

They are separate from the environment classes so the environments can call `device_relauncher`
without a cycle back through `environment_for`; both are re-exported from the package root.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from bajutsu import simctl
from bajutsu.config import Effective, require_ios
from bajutsu.drivers import base
from bajutsu.orchestrator import RelaunchFn
from bajutsu.platform_lifecycle import readiness
from bajutsu.scenario import Relaunch, Scenario

# Given a scenario + its launched driver, yields that scenario's `relaunch` function (defined here
# rather than imported from runner.types to keep the environment seam free of a runner import cycle).
RelaunchFactory = Callable[[Effective, Scenario, base.Driver], RelaunchFn]


def _web_relauncher(
    driver: base.Driver,
    ready_sel: base.Selector | None = None,
    id_namespaces: list[str] | None = None,
) -> RelaunchFn:
    """Web `relaunch`: re-navigate to the base URL and wait until ready (no device restart)."""

    def relaunch(opts: Relaunch) -> None:
        cast(base.BackendLifecycle, driver).navigate()  # web-only lifecycle
        readiness._await_ready(driver, ready_sel=ready_sel, id_namespaces=id_namespaces)

    return relaunch


def device_relauncher(
    udid: str, env_run: simctl.RunFn = simctl._real_run, extra_env: Mapping[str, str] | None = None
) -> RelaunchFactory:
    """A relauncher factory for the `relaunch` step.

    Restarts only the app process — terminate then launch again, re-applying the scenario's launch
    env/args plus any per-relaunch overrides, then wait until ready. The device is not erased or
    rebooted.

    Args:
        udid: The target device.
        env_run: The subprocess runner for simctl, injectable for tests.
        extra_env: Launch env re-applied across the relaunch (e.g. the device's collector url) so it
            survives; an explicit per-relaunch `env` override still wins over it.

    Returns:
        A factory that, given a scenario + driver, yields that scenario's `relaunch` function.
    """
    e = simctl.Env(udid, run=env_run)

    def for_scenario(eff: Effective, scenario: Scenario, driver: base.Driver) -> RelaunchFn:
        pre = scenario.preconditions
        bundle_id = require_ios(eff).bundle_id

        def relaunch(opts: Relaunch) -> None:
            e.terminate(bundle_id)
            launch_env = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
                **(opts.env or {}),
            }
            locale = pre.locale or eff.locale
            launch_args = [
                *eff.launch_args,
                *pre.launch_args,
                *(opts.args or []),
                *simctl.locale_args(locale),
            ]
            e.launch(bundle_id, launch_args, launch_env)
            readiness._await_ready(
                driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces
            )

        return relaunch

    return for_scenario

"""The `environment_for` factory — the seam that ends per-actuator branching in the runner."""

from __future__ import annotations

from bajutsu import simctl
from bajutsu.platform_lifecycle.environments.android import AndroidEnvironment
from bajutsu.platform_lifecycle.environments.fake import FakeEnvironment
from bajutsu.platform_lifecycle.environments.ios import IosEnvironment
from bajutsu.platform_lifecycle.environments.web import WebEnvironment
from bajutsu.platform_lifecycle.environments.xcuitest import XcuitestEnvironment
from bajutsu.platform_lifecycle.protocols import Environment, ProvisionProfile


def environment_for(
    actuator: str,
    udid: str,
    env_run: simctl.RunFn = simctl._real_run,
    *,
    provision: ProvisionProfile | None = None,
) -> Environment:
    """The `Environment` for *actuator* — the seam that ends per-actuator branching in the runner.

    `provision` carries a device provider's readiness report (BE-0236): a cloud device handed over
    already booted / with the app installed lets the environment skip that setup. None (the default,
    the local provider's inert profile) runs the full bring-up, so an omitted argument is unchanged.
    Only the Android environment consults it today; the others accept the run's default and ignore it.
    """
    if actuator == "playwright":
        return WebEnvironment(actuator)
    if actuator == "adb":
        # The Android environment drives adb (its own `argv -> stdout` runner), not simctl, so the
        # simctl-typed `env_run` does not apply — it uses adb's default runner.
        return AndroidEnvironment(actuator, udid, provision=provision)
    if actuator == "fake":
        return FakeEnvironment(actuator, udid, env_run)
    if actuator == "xcuitest":
        return XcuitestEnvironment(actuator, udid, env_run)
    return IosEnvironment(actuator, udid, env_run)

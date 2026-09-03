"""The XCUITest `_backend_launch` thunk shared by the on-device suites (BE-0342).

Kept out of `backend_crash_recovery.py`: that plugin's whole point is to stay backend-agnostic — a
suite opts in by exposing a `_backend_launch` fixture, and the plugin never learns which backend it
drives. `xcuitest_lease_launch` is the one concrete backend the tree ships an on-device suite for
today; a future Android on-device suite would add its own sibling here rather than teaching the
plugin a second backend name.
"""

from __future__ import annotations

from backend_crash_recovery import LeaseLaunch, LeaseTeardown

from bajutsu.common.config import Effective
from bajutsu.common.drivers import base
from bajutsu.common.platform_lifecycle import environment_for
from bajutsu.common.runner.launch import launch_driver


def xcuitest_lease_launch(udid: str, eff: Effective, *, extra_env: dict[str, str]) -> LeaseLaunch:
    """A `_backend_launch` thunk for the XCUITest backend: a fresh environment per lease, launched
    cold, whose teardown reaches the runner process (BE-0342).

    Shared by the driver conformance and fault-injection suites so their launch/teardown pairing —
    the contract `LeaseHolder` depends on — stays one definition rather than two that can drift.
    """

    def launch() -> tuple[base.Driver, LeaseTeardown]:
        env = environment_for("xcuitest", udid)
        driver, _readiness = launch_driver(
            udid, eff, "xcuitest", extra_env=extra_env, environment=env
        )
        return driver, lambda: env.teardown(driver, eff)

    return launch

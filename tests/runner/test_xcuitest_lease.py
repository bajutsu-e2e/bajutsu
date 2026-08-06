"""The XCUITest lease-launch thunk both on-device suites share (BE-0342).

`launch_driver` and `environment_for` themselves already have off-device coverage
(`tests/runner/test_launch.py`, `tests/platform_lifecycle/`); what only this module pins is the
pairing — that the returned teardown closes over the *same* environment `launch_driver` started,
tearing it down rather than leaving a `driver.close()` no-op. Neither an on-device suite nor mypy
(`make typecheck` runs over `bajutsu demos scripts`, not `tests/`) exercises this file otherwise.
"""

from __future__ import annotations

import pytest
import xcuitest_lease


def test_xcuitest_lease_launch_tears_down_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torn: list[tuple[object, object]] = []

    class _Env:
        def teardown(self, driver: object, eff: object) -> None:
            torn.append((driver, eff))

    env, sentinel, eff = _Env(), object(), object()
    monkeypatch.setattr(xcuitest_lease, "environment_for", lambda *a, **k: env)
    started_on: list[object] = []

    def _launch(*a: object, **k: object) -> tuple[object, None]:
        started_on.append(k.get("environment"))
        return sentinel, None

    monkeypatch.setattr(xcuitest_lease, "launch_driver", _launch)
    driver, teardown = xcuitest_lease.xcuitest_lease_launch("UDID-1", eff, extra_env={})()
    assert driver is sentinel
    assert started_on == [env]  # started on the very environment the teardown closes over
    teardown()
    assert torn == [(sentinel, eff)]  # the environment's own teardown, not driver.close()

"""Tests for the per-platform Environment seam (BE-0009 Phase 0).

The iOS simctl sequence is exercised through `launch_driver` in test_launch.py; these cover the
seam itself: the factory's actuator→Environment selection and the web/fake lifecycles that the
single-fork refactor folded behind the Protocol.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from _runner import _eff

from bajutsu import env
from bajutsu.drivers import base
from bajutsu.environment import (
    FakeEnvironment,
    IosEnvironment,
    WebEnvironment,
    environment_for,
)
from bajutsu.scenario import Preconditions


def test_environment_for_selects_by_actuator() -> None:
    assert isinstance(environment_for("idb", "UDID"), IosEnvironment)
    assert isinstance(environment_for("playwright", "UDID"), WebEnvironment)
    assert isinstance(environment_for("fake", "UDID"), FakeEnvironment)


def test_web_environment_requires_base_url() -> None:
    eff = replace(_eff(), base_url=None)
    with pytest.raises(env.DeviceError, match="baseUrl"):
        WebEnvironment("playwright").start(eff, Preconditions())


def test_web_environment_navigates_then_returns_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    class _WebDriver:
        name = "web"

        def __init__(self) -> None:
            self.navigated = False

        def navigate(self) -> None:
            self.navigated = True

        def query(self) -> list[base.Element]:
            return []

    web = _WebDriver()
    monkeypatch.setattr("bajutsu.environment.make_driver", lambda *a, **k: web)
    eff = replace(_eff(), base_url="https://app.test")
    driver = WebEnvironment("playwright").start(eff, Preconditions())
    assert driver is web
    assert web.navigated is True  # the web "launch" is navigate()


def test_fake_environment_runs_no_lifecycle() -> None:
    # No device lifecycle (no env_run, no simctl): it just yields the fake driver.
    driver = FakeEnvironment("fake", "UDID").start(_eff(), Preconditions())
    assert driver.query() == []  # the real fake driver, constructed without any device step


def test_ios_environment_surfaces_a_failing_step_as_device_error() -> None:
    import subprocess

    def fake_run(args: list[str], extra_env: object = None) -> str:
        if args[:3] == ["xcrun", "simctl", "erase"]:
            raise subprocess.CalledProcessError(1, args, output="", stderr="boom")
        return ""

    with pytest.raises(env.DeviceError):
        IosEnvironment("idb", "UDID", env_run=fake_run).start(_eff(), Preconditions(erase=True))

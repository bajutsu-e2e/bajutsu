"""Tests for the default live-driver factory shared by serve's capture/enrich sessions (BE-0127).

Every other serve test injects its own `driver_factory`, so the *default* one — the code path a
real `bajutsu serve` capture or enrich session actually takes — is the one nothing exercises. What
it gets right matters: a session must release whatever backs its driver, and XCUITest owns an
`xcodebuild` runner subprocess that only an explicit teardown stops (BE-0290). These tests pin both
halves of that contract, with the backend selection and the runner bring-up stubbed — the external
dependency — so the factory's own branching runs for real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.serve.operations._common import _close_quietly, _default_driver_factory


class _ClosingDriver(FakeDriver):
    """A driver that owns a resource its `close()` releases."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _eff() -> Effective:
    return resolve(load_config("targets:\n  demo:\n    bundleId: com.example.demo\n"), "demo")


# --- releasing a driver that owns no separate resource ---------------------------------------------


def test_close_quietly_calls_close_when_the_driver_has_one() -> None:
    driver = _ClosingDriver()
    _close_quietly(driver)
    assert driver.closed is True


def test_close_quietly_is_a_no_op_for_a_driver_without_close() -> None:
    # Most backends leave nothing to release once the driver is dropped, so the absence of `close()`
    # is a normal state, never an error.
    _close_quietly(FakeDriver())


# --- the default factory ---------------------------------------------------------------------------


def test_the_factory_brings_up_the_cheapest_requested_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[tuple[str, str]] = []

    def fake_make_driver(actuator: str, udid: str, **_k: object) -> base.Driver:
        made.append((actuator, udid))
        return FakeDriver()

    monkeypatch.setattr("bajutsu.common.backends.make_driver", fake_make_driver)
    driver, teardown = _default_driver_factory(_eff(), ["fake"], "udid-1")
    assert made == [("fake", "udid-1")]
    assert isinstance(driver, FakeDriver)
    # Teardown for a plain backend is the optional `close()`; it must not raise for a driver
    # that has none.
    teardown()


def test_the_factory_falls_back_to_the_fake_backend_when_none_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[tuple[str, str]] = []

    def fake_make_driver(actuator: str, udid: str, **_k: object) -> base.Driver:
        made.append((actuator, udid))
        return FakeDriver()

    monkeypatch.setattr("bajutsu.common.backends.make_driver", fake_make_driver)
    _default_driver_factory(_eff(), [], "")
    assert made == [("fake", "")]


def test_the_factory_releases_a_closable_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = _ClosingDriver()
    monkeypatch.setattr("bajutsu.common.backends.make_driver", lambda *_a, **_k: driver)
    _driver, teardown = _default_driver_factory(_eff(), ["fake"], "udid-1")
    assert driver.closed is False
    teardown()
    assert driver.closed is True


def test_an_xcuitest_session_tears_down_its_runner_rather_than_leaking_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """XCUITest owns an `xcodebuild` runner subprocess, so its teardown stops that runner (BE-0290).

    Dropping the driver is not enough here — the session must call the environment's own teardown,
    with the driver and the effective config it was started against.
    """
    eff = _eff()
    driver = FakeDriver()
    torn: list[tuple[object, object]] = []

    class _Env:
        def teardown(self, drv: object, cfg: object) -> None:
            torn.append((drv, cfg))

    def fake_open(udid: str, cfg: Effective, *_a: Any, **_k: Any) -> tuple[base.Driver, Any]:
        assert udid == "udid-9"
        assert cfg is eff
        return driver, _Env()

    monkeypatch.setattr("bajutsu.common.backends.select_actuator_cost_first", lambda _b: "xcuitest")
    monkeypatch.setattr(
        "bajutsu.common.platform_lifecycle.read_session.open_ios_read_driver", fake_open
    )
    monkeypatch.setattr(
        "bajutsu.common.backends.make_driver",
        lambda *_a, **_k: pytest.fail("xcuitest must not take the plain make_driver path"),
    )
    got, teardown = _default_driver_factory(eff, ["ios"], "udid-9")
    assert got is driver
    assert torn == []
    teardown()
    assert torn == [(driver, eff)]

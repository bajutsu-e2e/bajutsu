"""Shared harness for the run-pipeline test split: a fake Effective, fake driver, and a lease
over it with no per-device resources."""

from __future__ import annotations

from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import NullSink
from bajutsu.runner import Lease
from bajutsu.scenario import Redact, Scenario


def _eff() -> Effective:
    return Effective(
        target="demo",
        bundle_id="com.example.demo",
        deeplink_scheme=None,
        backend=["fake"],
        device="iPhone 15",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=["screenshot.after"],
        redact=Redact(),
    )


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _fake_driver() -> base.Driver:
    return FakeDriver([_el("ok", "OK", ["button"])])  # a screen that always contains "ok"


# A lease over a fake driver with no per-device resources (no evidence/network/control).
def _lease(eff: Effective, scenario: Scenario) -> Lease:
    return Lease(
        driver=_fake_driver(),
        sink=NullSink(),
        relaunch=None,
        control=None,
        collector=None,
        release=lambda: None,
    )


# A lease whose screen lacks "ok", so a `tap {id: ok}` step fails — used to model an engine that
# fails a scenario the others pass (the cross-browser incompatibility the matrix surfaces).
def _failing_lease(eff: Effective, scenario: Scenario) -> Lease:
    return Lease(
        driver=FakeDriver([_el("other", "Other", ["button"])]),
        sink=NullSink(),
        relaunch=None,
        control=None,
        collector=None,
        release=lambda: None,
    )

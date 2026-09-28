"""Tests for routing `interrupts` entries to the target each one watches (BE-0438).

The pipeline narrows a scenario's own `interrupts` to the entries each declared target watches — an
entry's `target`, or the primary when it omits one — before handing them to that target's runner,
whose interrupt guard then polls only its own device. Config-level entries stay with the target
whose config declared them. Each target's lease hands back a `FakeDriver` this test keeps a handle
on, so "which device cleared the overlay" is read straight off the driver's own action log.

The interrupt check is the assertion DSL, never a model call (prime directive 1).
"""

from __future__ import annotations

from dataclasses import replace

from _runner import _eff, _el, _web_eff

from bajutsu.common.config import Effective
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.runner import Lease, run_all
from bajutsu.common.runner.types import LeaseFn, TargetPool
from bajutsu.common.scenario import Interrupt, Scenario

_OVERLAY = "ov.close"
_CLEAR: dict[str, object] = {
    "condition": {"exists": {"id": _OVERLAY}},
    "steps": [{"tap": {"id": _OVERLAY}}],
}


def _overlaid() -> FakeDriver:
    """A screen showing an overlay over two buttons; tapping the overlay's close removes it."""

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"id": _OVERLAY}:
            d.screen = [e for e in d.screen if e["identifier"] != _OVERLAY]

    return FakeDriver(
        [_el(_OVERLAY, "X", ["button"]), _el("ok", "OK", ["button"]), _el("other", "Other")],
        react=react,
    )


def _lease_of(driver: FakeDriver) -> LeaseFn:
    def lease(eff: Effective, scenario: Scenario) -> Lease:
        return Lease(
            driver=driver,
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
        )

    return lease


def _cleared(driver: FakeDriver) -> bool:
    return ("tap", {"id": _OVERLAY}) in driver.actions


def _cross(interrupts: list[dict[str, object]]) -> Scenario:
    # `app` is declared first, so it is the primary.
    return Scenario.model_validate(
        {
            "name": "cross",
            "targets": ["app", "site"],
            "steps": [
                {"target": "app", "tap": {"id": "ok"}},
                {"target": "site", "tap": {"id": "other"}},
            ],
            "interrupts": interrupts,
        }
    )


def _run_cross(
    scenario: Scenario, *, site_eff: Effective | None = None
) -> tuple[FakeDriver, FakeDriver]:
    app, site = _overlaid(), _overlaid()
    targets = {
        "app": TargetPool(_eff(), _lease_of(app), "fake"),
        "site": TargetPool(site_eff or _web_eff(), _lease_of(site), "playwright"),
    }
    result = run_all(_eff(), [scenario], _lease_of(FakeDriver([])), targets=targets)[0]
    assert result.ok, result.failure
    return app, site


def test_an_entry_omitting_target_watches_only_the_primary() -> None:
    app, site = _run_cross(_cross([_CLEAR]))
    assert _cleared(app)
    assert not _cleared(site)  # the site's own guard never saw the entry


def test_an_entry_naming_a_target_watches_only_that_target() -> None:
    # The recovery tap omits `target`, so it runs on the runner whose guard fired — the site's.
    app, site = _run_cross(_cross([{**_CLEAR, "target": "site"}]))
    assert _cleared(site)
    assert not _cleared(app)


def test_a_config_level_entry_stays_with_its_own_target() -> None:
    # Declared under the site's own config: its recovery runs on the site, never on the primary.
    entry = Interrupt.model_validate(_CLEAR)
    site_eff = _web_eff()
    site_eff = replace(site_eff, run_defaults=replace(site_eff.run_defaults, interrupts=[entry]))
    app, site = _run_cross(_cross([]), site_eff=site_eff)
    assert _cleared(site)
    assert not _cleared(app)


# --- the ordinary single-target path is not filtered -------------------------------------------


def _single(extra: dict[str, object], interrupt: dict[str, object]) -> Scenario:
    return Scenario.model_validate(
        {"name": "single", "steps": [{"tap": {"id": "ok"}}], "interrupts": [interrupt], **extra}
    )


def test_interrupts_still_fire_with_no_declared_targets() -> None:
    driver = _overlaid()
    result = run_all(_eff(), [_single({}, _CLEAR)], _lease_of(driver))[0]
    assert result.ok, result.failure
    assert _cleared(driver)


def test_an_entry_naming_the_one_target_fires_with_no_target_map() -> None:
    # `_routed` is empty here (no `targets` map), so the primary name is "" — filtering on it would
    # compare "demo" against "" and silently drop the entry.
    driver = _overlaid()
    scenario = _single({"targets": ["demo"]}, {**_CLEAR, "target": "demo"})
    result = run_all(_eff(), [scenario], _lease_of(driver))[0]
    assert result.ok, result.failure
    assert _cleared(driver)

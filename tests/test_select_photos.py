"""`selectPhotos`: pick images from an open `PHPickerViewController` grid (roadmap item).

Every grid cell shares one identifier, `PXGGridLayout-Info`, disambiguated by ordinal `index` — the
same "nth of multiple matches" mechanism `handleSystemAlert` relies on for a SpringBoard button no
author-assignable identifier ever names. iOS-only, and narrower than "this backend": only a real
device or an Intel Simulator advertises `Capability.SELECT_PHOTOS` — `capabilities_for_run` drops
it on an Apple Silicon Simulator, where the grid's cells cannot be tapped reliably.

Covers the DSL parse + validation, the orchestrator dispatch to the driver, the fake's resolution
discipline, the preflight rejection on a backend without the capability, and the host-architecture
capability narrowing.
"""

from __future__ import annotations

import pytest

from bajutsu.common import backends
from bajutsu.common.capability.capability_preflight import unsupported
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import _action_of, run_scenario
from bajutsu.common.scenario import load_scenarios


def _cell(index: int) -> base.Element:
    """One grid cell — every cell shares the one identifier, disambiguated by `index` alone."""
    return {"identifier": "PXGGridLayout-Info", "label": f"Photo {index}", "traits": ["image"],
            "value": None, "frame": (0.0, float(index) * 100.0, 100.0, 100.0), "nativeZ": None}  # fmt: skip


# --- DSL parse + validation ---


def test_parse_select_photos() -> None:
    step = load_scenarios(
        "- name: t\n  steps:\n    - selectPhotos: { indices: [0, 1], timeout: 10 }\n"
    )[0].steps[0]
    assert step.select_photos is not None
    assert step.select_photos.indices == [0, 1]
    assert step.select_photos.timeout == 10
    assert _action_of(step) == "select_photos"


def test_select_photos_is_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        load_scenarios(
            "- name: t\n  steps:\n"
            "    - selectPhotos: { indices: [0], timeout: 10 }\n      tap: { id: b }\n"
        )


def test_select_photos_rejects_empty_indices() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        load_scenarios("- name: t\n  steps:\n    - selectPhotos: { indices: [], timeout: 10 }\n")


def test_select_photos_rejects_a_negative_index() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        load_scenarios("- name: t\n  steps:\n    - selectPhotos: { indices: [-1], timeout: 10 }\n")


def test_select_photos_rejects_a_duplicate_index() -> None:
    with pytest.raises(ValueError, match="must not repeat"):
        load_scenarios(
            "- name: t\n  steps:\n    - selectPhotos: { indices: [0, 0], timeout: 10 }\n"
        )


# --- Orchestrator dispatch: selectPhotos -> driver.select_photos(indices, timeout=...) ---


def test_dispatch_calls_driver_select_photos() -> None:
    driver = FakeDriver(screen=[_cell(0), _cell(1)])
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - selectPhotos: { indices: [0, 1], timeout: 10 }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert driver.actions == [("select_photos", (0, 1))]


def test_dispatch_fails_on_an_out_of_range_index() -> None:
    driver = FakeDriver(screen=[_cell(0)])
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - selectPhotos: { indices: [5], timeout: 10 }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert not result.ok
    assert driver.actions == []  # nothing was recorded as actuated


# --- The fake's own contract ---


def test_fake_select_photos_resolves_each_index_and_records_them() -> None:
    driver = FakeDriver(screen=[_cell(0), _cell(1), _cell(2)])
    driver.select_photos([2, 0], timeout=10)
    assert driver.actions == [("select_photos", (2, 0))]


def test_fake_select_photos_requires_a_resolvable_index() -> None:
    with pytest.raises(base.ElementNotFound):
        FakeDriver(screen=[_cell(0)]).select_photos([1], timeout=10)


# --- Preflight: a backend without the capability is rejected before any device work ---


def test_preflight_rejects_a_backend_without_the_select_photos_capability() -> None:
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - selectPhotos: { indices: [0], timeout: 10 }\n"
    )[0]
    assert base.Capability.SELECT_PHOTOS not in AdbDriver.CAPABILITIES
    reasons = unsupported(scenario, AdbDriver.CAPABILITIES)
    assert any("selectPhotos" in r for r in reasons)


def test_preflight_accepts_a_backend_that_advertises_the_capability() -> None:
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - selectPhotos: { indices: [0], timeout: 10 }\n"
    )[0]
    assert unsupported(scenario, FakeDriver.CAPABILITIES) == []


# --- capabilities_for_run: the Apple Silicon Simulator narrowing ---


def test_apple_silicon_simulator_host_reads_platform_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert backends.apple_silicon_simulator_host() is True
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    assert backends.apple_silicon_simulator_host() is False

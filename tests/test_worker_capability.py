"""The worker capability file and its check (BE-0450).

Loader: the shipped default, whole-file replacement, and every schema rejection. Check: the host
rule (local only), the `drivers` rule, the fallback list surviving on Linux, and the per-scenario
target limit — all pure, so no Simulator, browser, or emulator is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bajutsu.common import backends
from bajutsu.common.capability import capability_preflight as cp
from bajutsu.common.capability.worker_capability import (
    DEFAULT_WORKER_CONFIG,
    ENVIRONMENTS,
    WorkerCapability,
    WorkerCapabilityError,
    load_worker_capability,
    parse_worker_capability,
)
from bajutsu.common.scenario import Scenario


def _worker(**fields: object) -> WorkerCapability:
    return WorkerCapability.model_validate(
        {"version": 1, "drivers": ["xcuitest", "adb", "playwright", "fake"], **fields}
    )


def _sc(**body: object) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [], **body})


# --- loader -------------------------------------------------------------------------------------


def test_default_file_is_a_local_worker_with_every_driver() -> None:
    cap = load_worker_capability()
    assert cap.environment == "local"
    assert cap.is_local
    assert set(cap.drivers) == backends.IMPLEMENTED
    assert cap.max_job_concurrency == 1
    assert cap.max_targets_per_job is None
    assert DEFAULT_WORKER_CONFIG.is_file()


def test_passed_file_replaces_the_default_whole(tmp_path: Path) -> None:
    path = tmp_path / "w.yaml"
    path.write_text("version: 1\nmaxTargetsPerJob: 2\ndrivers: [playwright]\n", encoding="utf-8")
    cap = load_worker_capability(path)
    # Nothing is merged in from the default: the omitted drivers stay omitted.
    assert cap.drivers == ("playwright",)
    assert cap.max_targets_per_job == 2


def test_device_cloud_worker_may_run_several_jobs() -> None:
    cap = parse_worker_capability(
        "version: 1\nenvironment: devicefarm\nmaxJobConcurrency: 2\nmaxTargetsPerJob: 1\n"
        "drivers: [adb, xcuitest]\n"
    )
    assert not cap.is_local
    assert cap.max_job_concurrency == 2


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("version: 2\ndrivers: [fake]\n", "version"),
        ("drivers: [fake]\n", "version"),
        ("version: 1\ndrivers: [selenium]\n", "unknown driver(s) ['selenium']"),
        (
            "version: 1\nenvironment: saucelabs\ndrivers: [fake]\n",
            "unknown environment 'saucelabs'",
        ),
        ("version: 1\nmaxTargetsPerJob: 0\ndrivers: [fake]\n", "maxTargetsPerJob"),
        ("version: 1\nmaxJobConcurrency: -1\ndrivers: [fake]\n", "maxJobConcurrency"),
        ("version: 1\nmaxJobConcurrency: 2\ndrivers: [fake]\n", "a local worker runs one job"),
        ("version: 1\ndrivers: [fake]\nlabels: [gpu]\n", "labels"),
        ("- just a list\n", "mapping at the top level"),
        ("version: [1\n", "invalid YAML"),
    ],
)
def test_loader_rejects_an_invalid_file(text: str, fragment: str) -> None:
    with pytest.raises(WorkerCapabilityError, match=re.escape(fragment)):
        parse_worker_capability(text, source="bad.worker.yaml")


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(WorkerCapabilityError, match="cannot read worker config"):
        load_worker_capability(tmp_path / "absent.yaml")


def test_environments_match_the_registrable_batch_providers() -> None:
    # The core cannot import the server's provider registry, so the list is kept by hand; this
    # pins it to the one kind `batch_bootstrap` registers.
    source = Path("bajutsu/serve/batch_bootstrap.py").read_text(encoding="utf-8")
    kinds = set(re.findall(r'batch_provider\.register\("([a-z]+)"', source))
    assert ENVIRONMENTS - {"local"} == kinds


# --- driver host requirement --------------------------------------------------------------------


def test_only_xcuitest_is_bound_to_a_host() -> None:
    assert backends.host_os_for("xcuitest") == frozenset({"darwin"})
    for actuator in ("adb", "playwright", "fake"):
        assert backends.host_os_for(actuator) is None
    with pytest.raises(ValueError, match="unknown"):
        backends.host_os_for("selenium")


@pytest.mark.parametrize(
    ("platform", "host"),
    [("darwin", "darwin"), ("linux", "linux"), ("win32", "windows"), ("freebsd14", "freebsd14")],
)
def test_current_host_os_maps_sys_platform(platform: str, host: str) -> None:
    assert backends.current_host_os(platform) == host


# --- the check ----------------------------------------------------------------------------------


def test_xcuitest_on_linux_names_the_host() -> None:
    reason = cp.actuator_unavailable_reason("xcuitest", _worker(), "linux")
    assert reason == "xcuitest runs only on macOS; this worker runs on linux"
    assert cp.actuator_unavailable_reason("xcuitest", _worker(), "darwin") is None


def test_host_rule_does_not_apply_on_a_device_cloud() -> None:
    cloud = _worker(environment="devicefarm", drivers=["xcuitest"])
    assert cp.actuator_unavailable_reason("xcuitest", cloud, "linux") is None


def test_driver_missing_from_the_file_is_unavailable() -> None:
    reason = cp.actuator_unavailable_reason("playwright", _worker(drivers=["adb"]), "linux")
    assert reason == "playwright is not among this worker's drivers (adb)"


def test_filter_keeps_order_and_unimplemented_actuators() -> None:
    kept, reasons = cp.filter_actuators(["xcuitest", "flutter", "playwright"], _worker(), "linux")
    assert kept == ["flutter", "playwright"]
    assert reasons == ["xcuitest runs only on macOS; this worker runs on linux"]


def test_fallback_list_still_runs_on_linux() -> None:
    assert (
        cp.worker_capability_unsupported(_sc(), lambda _t: ["ios", "web"], _worker(), "linux") == []
    )


def test_ios_only_scenario_fails_on_linux() -> None:
    reasons = cp.worker_capability_unsupported(_sc(), lambda _t: ["ios"], _worker(), "linux")
    assert reasons == ["xcuitest runs only on macOS; this worker runs on linux"]
    assert cp.worker_capability_failure(reasons) == (
        "worker-capability-unsupported: xcuitest runs only on macOS; this worker runs on linux"
    )


def test_multi_target_failure_names_the_target() -> None:
    per_target = {"app": ["ios"], "site": ["web"]}
    reasons = cp.worker_capability_unsupported(
        _sc(targets=["app", "site"]), lambda t: per_target[t or ""], _worker(), "linux"
    )
    assert reasons == ["target 'app': xcuitest runs only on macOS; this worker runs on linux"]


def test_target_limit_counts_every_pool() -> None:
    limited = _worker(maxTargetsPerJob=1)
    expected = [
        "the scenario declares 2 targets; this worker allows at most 1 per job (maxTargetsPerJob)"
    ]
    # Two pools, and one device group of two: each holds two devices at once.
    for targets in (["app", "site"], [["phone_a", "phone_b"]]):
        two = _sc(targets=targets)
        reasons = cp.worker_capability_unsupported(two, lambda _t: ["fake"], limited, "darwin")
        assert reasons == expected
    assert cp.worker_capability_unsupported(_sc(), lambda _t: ["fake"], limited, "darwin") == []


def test_unbounded_worker_accepts_any_target_count() -> None:
    many = _sc(targets=["a", "b", "c"])
    assert cp.worker_capability_unsupported(many, lambda _t: ["fake"], _worker(), "linux") == []


def test_every_implemented_driver_declares_its_host() -> None:
    # A driver added to IMPLEMENTED without a `host_os_for` branch would raise mid-run.
    for actuator in sorted(backends.IMPLEMENTED):
        backends.host_os_for(actuator)


def test_an_injected_host_lookup_names_every_allowed_host() -> None:
    def lookup(actuator: str) -> frozenset[str] | None:
        return frozenset({"darwin", "linux"})

    reason = cp.actuator_unavailable_reason("adb", _worker(), "windows", lookup)
    assert reason == "adb runs only on macOS or Linux; this worker runs on windows"

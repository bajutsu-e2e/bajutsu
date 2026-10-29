"""The capability-token vocabulary that routes jobs to capable workers (BE-0166).

Pure functions, no Simulator: the platform axis, the required/advertised set algebra, the operator
override parsing, and the `simctl`-inventory derivation driven by captured `simctl` JSON.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from bajutsu.common.backend_cli import simctl
from bajutsu.serve import capabilities as cap


def test_required_capabilities_is_the_platform_axis_and_the_host_sorted() -> None:
    # The result is sorted so the routing key stored on a job row is canonical. The hand-declared
    # `requires` tokens are gone with the target-config restructure (BE-0452).
    assert cap.required_capabilities("ios", host="darwin") == ["host:darwin", "platform:ios"]


def test_required_capabilities_without_platform_is_empty() -> None:
    # A job with no resolvable platform (e.g. a config load error) carries no bogus `platform:` token.
    assert cap.required_capabilities("") == []


def test_can_serve_is_a_subset_test() -> None:
    # A worker may run a job only when it advertises every token the job requires.
    assert cap.can_serve(["platform:ios", "ios18"], ["platform:ios", "ios18", "ipad"])
    assert not cap.can_serve(["ios18"], ["platform:ios", "ios17"])  # ios18 ⊄ ios17-only worker
    assert not cap.can_serve(["platform:web"], ["platform:ios"])  # web ⊄ iOS worker


def test_empty_requirement_is_servable_by_anyone() -> None:
    # An un-annotated job (empty required set) leases to any worker — the pre-routing behavior.
    assert cap.can_serve([], ["platform:ios"])
    assert cap.can_serve([], [])


def test_parse_capabilities_splits_on_comma_and_whitespace() -> None:
    assert cap.parse_capabilities("ios18, ipad  platform:ios") == {
        "ios18",
        "ipad",
        "platform:ios",
    }
    assert cap.parse_capabilities("") == set()
    assert cap.parse_capabilities(None) == set()


def _fake_simctl(devices: dict[str, list[dict[str, str]]]) -> simctl.RunFn:
    """A `simctl` RunFn returning a canned `list devices` JSON payload (keyed by runtime id)."""

    def run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        return json.dumps({"devices": devices})

    return run


def test_worker_capabilities_combines_platform_override_and_inventory() -> None:
    run = _fake_simctl(
        {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-1": [
                {"udid": "U1", "name": "iPhone 15", "state": "Shutdown"},
                {"udid": "U2", "name": "iPad Pro (11-inch)", "state": "Shutdown"},
            ]
        }
    )
    caps = cap.worker_capabilities(["ios"], override="beta, ipad", run=run)
    # platform axis + override tokens + inventory-derived runtime (ios18) and device classes.
    assert caps == {"platform:ios", "beta", "ipad", "ios18", "iphone"}


def test_worker_capabilities_without_inventory_is_platform_plus_override() -> None:
    # The web container has no Simulators (no `run`): it advertises only its platform + override.
    assert cap.worker_capabilities(["web"], override=None, run=None) == {"platform:web"}


def test_simctl_capabilities_is_empty_on_failure() -> None:
    def boom(args, extra_env=None):
        raise OSError("no xcrun")

    assert cap.simctl_capabilities(boom) == set()


def test_simctl_capabilities_derives_major_version_only() -> None:
    run = _fake_simctl(
        {
            "com.apple.CoreSimulator.SimRuntime.iOS-17-5": [
                {"udid": "U1", "name": "iPhone SE", "state": "Shutdown"}
            ]
        }
    )
    # A job pinned to `ios17` matches any 17.x runtime — the minor version is dropped.
    assert cap.simctl_capabilities(run) == {"ios17", "iphone"}


def test_an_environment_worker_serves_only_jobs_that_require_its_token() -> None:
    # BE-0448: the subset test alone would hand a Device Farm worker every job requiring nothing.
    df = ["environment:devicefarm"]
    assert cap.environment_capability("devicefarm") == "environment:devicefarm"
    assert cap.can_serve(["environment:devicefarm"], df)
    assert not cap.can_serve([], df)
    assert not cap.can_serve(["platform:ios"], [*df, "platform:ios"])
    # A plain worker never covers the reserved token, so it never serves a batch job.
    assert not cap.can_serve(["environment:devicefarm"], ["platform:ios", "platform:android"])
    assert cap.can_serve([], ["platform:ios"])


def test_reserved_tokens_names_the_environment_and_host_prefixes_alone() -> None:
    assert cap.reserved_tokens(["ios18", "host:linux", "environment:x", "platform:ios"]) == [
        "environment:x",
        "host:linux",
    ]


def test_reserved_tokens_names_the_device_prefix() -> None:
    # A hand-written `device:` token could claim a real device the worker lacks (BE-0468).
    assert cap.reserved_tokens(["ipad", "device:ipad"]) == ["device:ipad"]


def test_an_any_of_group_is_met_by_any_one_member() -> None:
    group = cap.any_of_capability(["device:ipad", "device:iphone"])
    assert group == "anyof:device:ipad|device:iphone"
    assert cap.can_serve([group, "platform:ios"], ["platform:ios", "device:iphone"])
    assert cap.can_serve([group], ["device:ipad", "device:iphone"])
    assert not cap.can_serve([group], ["device:watch", "ipad"])
    # The plain tokens beside a group still all have to be advertised.
    assert not cap.can_serve([group, "platform:ios"], ["device:ipad"])


def test_an_any_of_group_is_canonical_and_a_lone_member_stands_as_itself() -> None:
    assert cap.any_of_capability(["b", "a", "b"]) == cap.any_of_capability(["a", "b"])
    assert cap.any_of_capability(["device:ipad"]) == "device:ipad"
    with pytest.raises(ValueError, match="at least one"):
        cap.any_of_capability([])


def test_device_requirements_route_each_kind_on_its_own_tokens() -> None:
    # No family, no requirement: the target takes any device, as before BE-0468.
    assert cap.device_requirements(["simulator", "device"], None) == []
    # One family requires that family's token, on the axis of each kind the job runs.
    assert cap.device_requirements(["device"], "ipad") == ["device:ipad"]
    assert cap.device_requirements(["simulator"], "ipad") == ["ipad"]
    assert cap.device_requirements(["simulator", "device"], ["ipad"]) == ["device:ipad", "ipad"]
    # A priority list accepts any one family, so it becomes a group per kind.
    assert cap.device_requirements(["simulator", "device"], ["iphone", "ipad"]) == [
        "anyof:device:ipad|device:iphone",
        "anyof:ipad|iphone",
    ]


def test_devicectl_capabilities_advertises_each_known_family() -> None:
    assert cap.devicectl_capabilities(lambda: {"iPhone", "iPad", "Apple TV"}) == {
        "device:iphone",
        "device:ipad",
    }


def test_devicectl_capabilities_is_empty_when_devicectl_cannot_be_read() -> None:
    from bajutsu.common.devices.facts import DeviceReadError

    def broken() -> set[str]:
        raise DeviceReadError("no xcrun")

    assert cap.devicectl_capabilities(broken) == set()

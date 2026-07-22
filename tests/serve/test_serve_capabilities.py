"""The capability-token vocabulary that routes jobs to capable workers (BE-0166).

Pure functions, no Simulator: the platform axis, the required/advertised set algebra, the operator
override parsing, and the `simctl`-inventory derivation driven by captured `simctl` JSON.
"""

from __future__ import annotations

import json

from bajutsu.serve import capabilities as cap


def test_required_capabilities_is_platform_axis_plus_declared_sorted() -> None:
    # The platform axis is always present; declared tokens add to it; the result is sorted + deduped
    # so the routing key stored on a job row is canonical.
    assert cap.required_capabilities("ios", ["ipad", "ios18", "ipad"]) == [
        "ios18",
        "ipad",
        "platform:ios",
    ]


def test_required_capabilities_without_platform_is_just_declared() -> None:
    # A job with no resolvable platform (e.g. a config load error) carries only what was declared —
    # never a bogus `platform:` token.
    assert cap.required_capabilities("", ["ios18"]) == ["ios18"]
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


def _fake_simctl(devices: dict[str, list[dict[str, str]]]):
    """A `simctl` RunFn returning a canned `list devices` JSON payload (keyed by runtime id)."""

    def run(args, extra_env=None):
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

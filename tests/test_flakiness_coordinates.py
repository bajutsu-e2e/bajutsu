"""The flakiness history keys a run by its target and `runsOn` coordinates too (BE-0452).

One scenario that passes on an iPhone and fails on an iPad is two deterministic histories, not one
flaky one: a layout failure on one model must never read as flakiness.
"""

from __future__ import annotations

from bajutsu.analysis.flakiness import rank_flakiness, records_from_manifests, render
from bajutsu.common.devices.runs import (
    MIXED_COORDINATES,
    coordinates_from_manifest,
    coordinates_key,
)
from bajutsu.serve.server.db import RunRecord

_HASH = "sha256:c6b97d3d1"
_SE = {"app": {"model": "iPhone SE (3rd generation)"}}
_IPAD = {"app": {"model": "iPad Pro 13-inch (M4)"}}


def _manifest(
    run_id: str, *, ok: bool, coordinates: dict[str, dict[str, str]]
) -> dict[str, object]:
    return {
        "schemaVersion": 15,
        "runId": run_id,
        "ok": ok,
        "target": "app",
        "provenance": {"scenarioHash": _HASH},
        "scenarios": [
            {
                "scenario": "login",
                "ok": ok,
                "device_runtime": "iOS 18.2",
                "run_coordinates": coordinates,
            }
        ],
    }


def test_coordinates_key_is_canonical() -> None:
    assert coordinates_key({"b": {"os": "iOS 18"}, "a": {"os": "iOS 17", "model": "X"}}) == (
        "a: model=X, os=iOS 17; b: os=iOS 18"
    )
    assert coordinates_key({}) == ""


def test_coordinates_from_manifest_is_unanimous_or_mixed() -> None:
    assert coordinates_from_manifest(_manifest("r", ok=True, coordinates=_SE)) == (
        "app: model=iPhone SE (3rd generation)"
    )
    assert coordinates_from_manifest(_manifest("r", ok=True, coordinates={})) == ""
    mixed = _manifest("r", ok=True, coordinates=_SE)
    first = _manifest("r", ok=True, coordinates=_IPAD)["scenarios"]
    assert isinstance(first, list) and isinstance(mixed["scenarios"], list)
    mixed["scenarios"] = [*mixed["scenarios"], *first]
    assert coordinates_from_manifest(mixed) == MIXED_COORDINATES
    # A manifest from before coordinates were recorded could carry no condition: it reads as empty.
    older = _manifest("r", ok=True, coordinates={})
    older["schemaVersion"] = 13
    for s in older["scenarios"]:  # type: ignore[attr-defined]
        s.pop("run_coordinates")
    assert coordinates_from_manifest(older) == ""


def test_one_scenario_on_two_models_is_two_deterministic_histories() -> None:
    manifests = [
        _manifest(f"20261014-00000{i}-a", ok=coords is _SE, coordinates=coords)
        for i, coords in enumerate([_SE, _IPAD, _SE, _IPAD])
    ]
    report = rank_flakiness(records_from_manifests(manifests))
    assert sorted((s.coordinates, s.classification, s.target) for s in report.scenarios) == [
        ("app: model=iPad Pro 13-inch (M4)", "deterministic", "app"),
        ("app: model=iPhone SE (3rd generation)", "deterministic", "app"),
    ]
    assert "[app: model=iPad Pro 13-inch (M4)]" in render(report)


def test_hosts_whose_family_list_resolves_apart_keep_apart_histories() -> None:
    # BE-0468: `family: [iphone, ipad]` takes an iPad on a host without an iPhone, so the winner
    # keys the history and an iPad-only layout failure does not read as flakiness.
    iphone = {"app": {"kind": "simulator", "family": "iphone"}}
    ipad = {"app": {"kind": "simulator", "family": "ipad"}}
    manifests = [
        _manifest(f"20261029-00000{i}-a", ok=coords is iphone, coordinates=coords)
        for i, coords in enumerate([iphone, ipad, iphone, ipad])
    ]
    report = rank_flakiness(records_from_manifests(manifests))
    assert sorted((s.coordinates, s.classification) for s in report.scenarios) == [
        ("app: family=ipad, kind=simulator", "deterministic"),
        ("app: family=iphone, kind=simulator", "deterministic"),
    ]


def test_two_targets_are_two_histories() -> None:
    def record(run_id: str, target: str, ok: bool) -> RunRecord:
        return RunRecord(
            id=run_id, org_id="o", status="done", ok=ok, scenario_hash=_HASH, target=target
        )

    report = rank_flakiness(
        [record("1", "ios", True), record("2", "android", False), record("3", "ios", True)]
    )
    assert sorted((s.target, s.classification) for s in report.scenarios) == [
        ("android", "unproven"),
        ("ios", "deterministic"),
    ]


def test_an_unconditioned_history_spans_the_upgrade() -> None:
    older = _manifest("20261013-000001-a", ok=True, coordinates={})
    older["schemaVersion"] = 13
    newer = _manifest("20261014-000001-a", ok=True, coordinates={})
    (scenario,) = rank_flakiness(records_from_manifests([older, newer])).scenarios
    assert (scenario.runs, scenario.coordinates) == (2, "")


def _row(run_id: str, ok: bool, coordinates: str | None) -> RunRecord:
    return RunRecord(
        id=run_id,
        org_id="o",
        status="done",
        ok=ok,
        scenario_hash=_HASH,
        target="app",
        run_coordinates=coordinates,
    )


def test_a_null_row_joins_the_empty_key_and_a_mixed_row_stays_apart() -> None:
    report = rank_flakiness(
        [_row("1", True, None), _row("2", True, ""), _row("3", False, MIXED_COORDINATES)]
    )
    assert sorted((s.coordinates, s.runs) for s in report.scenarios) == [("", 2), ("*", 1)]

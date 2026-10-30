"""`seedPhotos`: seed the iOS Simulator's photo library before `selectPhotos` addresses it (roadmap item).

Unlike `privacy` / `push`, `simctl addmedia` is device-scoped and adds a library entry on every
call rather than being idempotent, so `Env.add_media` issues one call per path in order — nothing
documents the relative order a single call handed several paths would assign them. The run checks
that a merged, non-empty `seedPhotos` comes with an effective `run.erase` (`check_preconditions`),
so a scenario can never silently seed nothing and fall back to whatever the Simulator's ambient
library contains.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from bajutsu.common.backend_cli import simctl
from bajutsu.common.config import (
    Effective,
    check_preconditions,
    load_config,
    resolve,
    resolve_preconditions,
)
from bajutsu.common.scenario import Preconditions, load_scenarios


def test_addmedia_command_builder() -> None:
    assert simctl.addmedia_cmd("U", "/fixtures/red.png") == [
        "xcrun",
        "simctl",
        "addmedia",
        "U",
        "/fixtures/red.png",
    ]


def test_addmedia_cmd_rejects_an_unvalidated_udid() -> None:
    with pytest.raises(simctl.DeviceError):
        simctl.addmedia_cmd("-x", "/fixtures/red.png")


def test_env_add_media_issues_one_call_per_path_in_order() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append(args)
        return ""

    e = simctl.Env("UDID", run=fake_run)
    e.add_media(["/fixtures/red.png", "/fixtures/green.png"])

    assert calls == [
        ["xcrun", "simctl", "addmedia", "UDID", "/fixtures/red.png"],
        ["xcrun", "simctl", "addmedia", "UDID", "/fixtures/green.png"],
    ]


def test_env_add_media_of_an_empty_list_calls_nothing() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append(args)
        return ""

    simctl.Env("UDID", run=fake_run).add_media([])
    assert calls == []


# --- the merged seedPhotos needs an effective erase (BE-0452) ---


def _eff(target_lines: str = "") -> Effective:
    text = f"targets:\n  app:\n    platform: ios\n    subject: {{ id: com.example.app }}\n{target_lines}"
    return resolve(load_config(text), "app")


def _pre(text: str) -> Preconditions:
    return load_scenarios(f"- name: t\n  preconditions: {text}\n  steps: []\n")[0].preconditions


_SEED = "device: { ios: { seedPhotos: [/fixtures/red.png] } }"


def test_seed_photos_without_erase_is_a_problem() -> None:
    problems = check_preconditions(_pre(f"{{ {_SEED} }}"), [_eff()])
    assert len(problems) == 1
    assert "seedPhotos on target 'app' needs a wiped device" in problems[0]


def test_seed_photos_with_the_scenario_erase_is_accepted() -> None:
    assert check_preconditions(_pre(f"{{ {_SEED}, run: {{ erase: true }} }}"), [_eff()]) == []


def test_a_target_erase_satisfies_the_scenario_seed_photos() -> None:
    eff = _eff("    run: { erase: true }\n")
    assert check_preconditions(_pre(f"{{ {_SEED} }}"), [eff]) == []


def test_the_erase_flag_wins_over_the_scenario() -> None:
    pre = _pre(f"{{ {_SEED}, run: {{ erase: true }} }}")
    assert check_preconditions(pre, [_eff()], erase_flag=False)
    assert check_preconditions(_pre(f"{{ {_SEED} }}"), [_eff()], erase_flag=True) == []


def test_target_seed_photos_merge_under_the_scenario() -> None:
    eff = _eff("    runsOn: {ios: {seedPhotos: [/fixtures/green.png]}}\n")
    # The target's own list reaches a scenario that writes none, and needs the same erase.
    assert resolve_preconditions(eff, _pre("{}")).seed_photos == ["/fixtures/green.png"]
    assert check_preconditions(_pre("{}"), [eff])
    # A scenario's list replaces the target's, so an empty one seeds nothing and needs no erase.
    empty = _pre("{ device: { ios: { seedPhotos: [] } } }")
    assert resolve_preconditions(eff, empty).seed_photos == []
    assert check_preconditions(empty, [eff]) == []


def test_no_seed_photos_needs_no_erase() -> None:
    assert check_preconditions(Preconditions(), [_eff()]) == []


def test_parse_seed_photos_in_yaml() -> None:
    scenario = load_scenarios(
        "- name: t\n"
        "  preconditions:\n"
        "    run: { erase: true }\n"
        "    device: { ios: { seedPhotos: [fixtures/red.png, fixtures/green.png] } }\n"
        "  steps:\n    - tap: { id: a }\n"
    )[0]
    assert scenario.preconditions.seed_photos_for("ios") == [
        "fixtures/red.png",
        "fixtures/green.png",
    ]

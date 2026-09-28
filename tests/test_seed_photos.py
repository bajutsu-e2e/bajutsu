"""`seedPhotos`: seed the iOS Simulator's photo library before `selectPhotos` addresses it (roadmap item).

Unlike `privacy` / `push`, `simctl addmedia` is device-scoped and adds a library entry on every
call rather than being idempotent, so `Env.add_media` issues one call per path in order — nothing
documents the relative order a single call handed several paths would assign them. `Preconditions`'s
own validator requires `erase: true` alongside a non-empty `seedPhotos`, so a scenario can never
silently seed nothing and fall back to whatever the Simulator's ambient library contains.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from bajutsu.common.backend_cli import simctl
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


# --- Preconditions: seedPhotos requires erase: true ---


def test_seed_photos_requires_erase() -> None:
    with pytest.raises(ValueError, match="requires preconditions"):
        Preconditions(seedPhotos=["/fixtures/red.png"])


def test_seed_photos_with_erase_false_still_rejected() -> None:
    with pytest.raises(ValueError, match="requires preconditions"):
        Preconditions(erase=False, seedPhotos=["/fixtures/red.png"])


def test_seed_photos_with_erase_true_is_accepted() -> None:
    pre = Preconditions(erase=True, seedPhotos=["/fixtures/red.png"])
    assert pre.seed_photos == ["/fixtures/red.png"]


def test_no_seed_photos_needs_no_erase() -> None:
    Preconditions()  # no raise: the validator only fires when seedPhotos is non-empty


def test_parse_seed_photos_in_yaml() -> None:
    scenario = load_scenarios(
        "- name: t\n"
        "  preconditions: { erase: true, seedPhotos: [fixtures/red.png, fixtures/green.png] }\n"
        "  steps:\n    - tap: { id: a }\n"
    )[0]
    assert scenario.preconditions.seed_photos == ["fixtures/red.png", "fixtures/green.png"]


def test_parse_seed_photos_without_erase_rejected_in_yaml() -> None:
    with pytest.raises(ValueError, match="requires preconditions"):
        load_scenarios(
            "- name: t\n"
            "  preconditions: { seedPhotos: [fixtures/red.png] }\n"
            "  steps:\n    - tap: { id: a }\n"
        )

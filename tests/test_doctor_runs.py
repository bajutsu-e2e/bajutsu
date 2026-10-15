"""`doctor` lists each scenario's runs and the devices that fit them (BE-0452 unit 11).

The listing forms the runs `run` would form against the same pool, so it reads the pool through
fakes here; with no pool to read, it lists the declared runs alone. The screen probe and the MCP
doctor tool take the first run's device rather than refusing a condition.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli.commands.doctor import runs_summary
from bajutsu.common.capability.worker_capability import parse_worker_capability
from bajutsu.common.config import DeviceProvider, Effective, load_config, resolve
from bajutsu.common.devices.errors import DeviceError
from bajutsu.common.devices.facts import DeviceFacts
from bajutsu.common.devices.runs import declared_runs
from bajutsu.run.device_matrix import first_run_device, list_runs

_SE = "iPhone SE (3rd generation)"
_IPAD = "iPad Pro 13-inch (M4)"
_POOL = [
    DeviceFacts(udid="se17", platform="ios", model=_SE, release="17.5"),
    DeviceFacts(udid="se18", platform="ios", model=_SE, release="18.2"),
    DeviceFacts(udid="ipad18", platform="ios", model=_IPAD, release="18.2"),
]
_ANDROID_POOL = [
    DeviceFacts(udid="emulator-5554", platform="android", model="Pixel", release="34", avd="P34"),
]


def _eff(runs_on: str, platform: str = "ios", app_id: str = "{ id: com.x }") -> Effective:
    text = f"targets:\n  app: {{ platform: {platform}, app: {app_id}, runsOn: {runs_on} }}\n"
    return resolve(load_config(text), "app")


def _reader(pool: Sequence[DeviceFacts]) -> Any:
    def read(_eff: Effective, _actuator: str, _udid: str, _engines: list[str]) -> list[DeviceFacts]:
        return list(pool)

    return read


# --- the listing ----------------------------------------------------------------------------


def test_lists_each_run_with_the_devices_that_fit_it() -> None:
    eff = _eff(f'{{ model: ["{_SE}", "{_IPAD}"], os: ">=17 <19" }}')
    lines = runs_summary(eff, "xcuitest", "se17,se18,ipad18", "", read_pool=_reader(_POOL))
    assert lines == [
        "runs:",
        "  target 'app':",
        f'    ✓ {_SE} / iOS 17: iOS 17.5 "{_SE}" (se17)',
        f'    ✓ {_SE} / iOS 18: iOS 18.2 "{_SE}" (se18)',
        f"    ✘ {_IPAD} / iOS 17: fails: no available device fits this run",
        f'    ✓ {_IPAD} / iOS 18: iOS 18.2 "{_IPAD}" (ipad18)',
    ]


def test_a_missing_model_fails_and_an_open_range_is_not_applicable() -> None:
    missing = runs_summary(_eff('{ model: ["iPhone 99"] }'), "xcuitest", "u", "", _reader(_POOL))
    assert missing[2].startswith('    ✘ iPhone 99: fails: runsOn.ios.model  "iPhone 99"')
    later = runs_summary(_eff('{ os: ">=19" }'), "xcuitest", "u", "", _reader(_POOL))
    assert later[2].startswith('    – one run: not applicable: runsOn.ios.os  ">=19"')


def test_an_open_range_runs_once_per_major_the_pool_has() -> None:
    lines = runs_summary(_eff('{ os: ">=17" }'), "xcuitest", "u", "", _reader(_POOL))
    assert [line.split(":")[0] for line in lines[2:]] == ["    ✓ iOS 17", "    ✓ iOS 18"]


def test_each_scenario_of_the_file_merges_its_block_over_the_target(tmp_path: Path) -> None:
    scenarios = tmp_path / "s.yaml"
    scenarios.write_text(
        "- name: login\n  steps: [{ tap: { id: a } }]\n"
        "- name: split\n"
        '  preconditions: { runsOn: { ios: { model: "' + _IPAD + '" } } }\n'
        "  steps: [{ tap: { id: a } }]\n",
        encoding="utf-8",
    )
    lines = runs_summary(_eff('{ os: "18" }'), "xcuitest", "u", str(scenarios), _reader(_POOL))
    assert lines == [
        "runs:",
        "  login:",
        f'    ✓ iOS 18: iOS 18.2 "{_SE}" (se18)',
        "  split:",
        f'    ✓ iOS 18: iOS 18.2 "{_IPAD}" (ipad18)',
    ]


def test_android_lists_by_avd_and_api_level() -> None:
    eff = _eff('{ avd: ["P34", "P35"], apiLevel: "34" }', "android", "{ id: com.x }")
    lines = runs_summary(eff, "adb", "emulator-5554", "", _reader(_ANDROID_POOL))
    assert lines[2] == '    ✓ P34 / API 34: API level 34 "Pixel" (emulator-5554)'
    assert lines[3].startswith('    ✘ P35: fails: runsOn.android.avd  "P35"')


def test_without_a_condition_or_under_fake_nothing_is_listed() -> None:
    assert runs_summary(_eff("{}"), "xcuitest", "u", "", _reader(_POOL)) == []
    assert runs_summary(_eff('{ os: "18" }'), "fake", "u", "", _reader(_POOL)) == []


# --- no pool to read ------------------------------------------------------------------------


def test_an_unreadable_pool_lists_the_declared_runs() -> None:
    def broken(*_a: Any) -> list[DeviceFacts]:
        raise DeviceError("simctl is not installed")

    eff = _eff(f'{{ model: ["{_SE}", "{_IPAD}"], os: "17 || >=19" }}')
    lines = runs_summary(eff, "xcuitest", "u", "", read_pool=broken)
    assert lines == [
        "runs:",
        "  declared runs only — could not read the device pool: simctl is not installed",
        "  target 'app':",
        f"    • {_SE} / iOS 17",
        f'    • {_SE} / one run per available major version in os "17 || >=19"',
        f"    • {_IPAD} / iOS 17",
        f'    • {_IPAD} / one run per available major version in os "17 || >=19"',
    ]


def test_a_grid_target_has_no_pool_and_names_why() -> None:
    eff = replace(
        _eff('{ os: ">=18" }'),
        device_provider=DeviceProvider(kind="appium", endpoint="https://grid.example/wd/hub"),
    )
    lines = runs_summary(eff, "xcuitest", "u", "", _reader(_POOL))
    assert lines[1].startswith(
        "  declared runs only — runsOn.ios.os: target 'app' runs on a remote"
    )
    assert lines[3] == '    • one run per available major version in os ">=18"'


@pytest.mark.parametrize(
    ("platform", "conditions", "expected"),
    [
        ("ios", {}, [""]),
        ("ios", {"model": "iPhone 15"}, [""]),
        ("ios", {"os": ">=17 <19"}, ["iOS 17", "iOS 18"]),
        ("android", {"apiLevel": "33 - 34"}, ["API 33", "API 34"]),
        ("web", {"browser.engine": ["chromium", "webkit"]}, ["chromium", "webkit"]),
        ("web", {"browser.version": ">=120"}, [""]),
    ],
)
def test_declared_runs(
    platform: str, conditions: dict[str, str | list[str]], expected: list[str]
) -> None:
    assert declared_runs(platform, conditions, engine="chromium") == expected


def test_a_web_run_names_the_browser_it_launches() -> None:
    web = "{ url: 'http://x/' }"
    listed = list_runs(*_web("{ browser: { engine: [chromium, webkit] } }", web), [])
    assert [(r.label, r.outcome, r.devices) for r in listed] == [
        ("chromium", "run", ("chromium",)),
        ("webkit", "run", ("webkit",)),
    ]
    browser = DeviceFacts(udid="webkit", platform="web", model="webkit", release="18.0")
    (ranged,) = list_runs(*_web('{ browser: { engine: webkit, version: ">=17" } }', web), [browser])
    assert ranged.devices == (browser.describe,)
    (excluded,) = list_runs(
        *_web('{ browser: { engine: webkit, version: ">=19" } }', web), [browser]
    )
    assert excluded.outcome == "not-applicable"


def _web(runs_on: str, app_id: str) -> tuple[Effective, dict[str, str | list[str]]]:
    eff = _eff(runs_on, "web", app_id)
    return eff, dict(eff.conditions)


# --- the screen probe and the MCP doctor tool -----------------------------------------------


def test_the_first_run_device_takes_the_newest_major_in_range() -> None:
    eff = _eff(f'{{ model: "{_SE}", os: ">=17" }}')
    _, udid = first_run_device(eff, "xcuitest", "se17,se18", read_ios=lambda _u: list(_POOL))
    assert udid == "se18"
    with pytest.raises(ValueError, match="no available device fits"):
        first_run_device(
            _eff('{ os: ">=20" }'), "xcuitest", "se17", read_ios=lambda _u: list(_POOL)
        )
    # The fake actuator matches nothing and keeps the udid as given.
    assert first_run_device(eff, "fake", "booted") == (eff, "booted")


def test_the_mcp_doctor_tool_scores_the_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import FastMCP

    from bajutsu.mcp.tools import register_tools

    probed: list[str] = []

    def probe(_actuator: str, udid: str, _eff: Effective) -> list[object]:
        probed.append(udid)
        return []

    monkeypatch.setattr("bajutsu.mcp.tools.probe_screen", probe)
    monkeypatch.setattr("bajutsu.run.device_matrix.target_pool", lambda *_a, **_k: list(_POOL))
    monkeypatch.setattr("bajutsu.mcp.tools.select_actuator", lambda _b: "xcuitest")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        'targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { os: ">=17" } }\n',
        encoding="utf-8",
    )
    mcp = FastMCP("test")
    register_tools(mcp, cfg, worker=parse_worker_capability("version: 1\ndrivers: [xcuitest]\n"))
    result = asyncio.run(mcp.call_tool("bajutsu_doctor", {"target": "t", "udid": "se17,se18"}))
    text = getattr(result.content[0], "text", "")
    assert "not yet supported" not in text
    assert probed == ["se18"]


# --- the command ----------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["linux", "darwin"])
def test_the_command_prints_the_runs_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    monkeypatch.setattr(
        "bajutsu.common.capability.preflight.doctor_environment_checks", lambda *a, **k: []
    )
    monkeypatch.setattr("bajutsu.cli._shared.current_host_os", lambda: host)
    monkeypatch.setattr("bajutsu.cli.commands.doctor._host_toolchain", lambda: (None, None))
    # CI hosts have no adb; the runs section is what this test reads, not the toolchain.
    monkeypatch.setattr("bajutsu.cli.commands.doctor.select_actuator", lambda *_a, **_k: "adb")
    android = [
        DeviceFacts(
            udid="emulator-5554", platform="android", model="Pixel", release="34", avd="P34"
        ),
        DeviceFacts(
            udid="emulator-5556", platform="android", model="Pixel", release="35", avd="P35"
        ),
    ]
    monkeypatch.setattr("bajutsu.cli.commands.doctor.target_pool", lambda *_a, **_k: android)
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        'targets:\n  t: { platform: android, app: { id: com.x }, runsOn: { apiLevel: ">=34" } }\n',
        encoding="utf-8",
    )
    argv = ["doctor", "--target", "t", "--config", str(cfg), "--environment-only"]
    result = CliRunner().invoke(app, [*argv, "--udid", "emulator-5554,emulator-5556"])
    assert result.exit_code == 0, result.output
    assert (
        "runs:\n  target 't':\n"
        '    ✓ API 34: API level 34 "Pixel" (emulator-5554)\n'
        '    ✓ API 35: API level 35 "Pixel" (emulator-5556)\n'
    ) in result.output


def test_a_malformed_scenario_range_is_named_rather_than_crashing(tmp_path: Path) -> None:
    scenarios = tmp_path / "s.yaml"
    scenarios.write_text(
        '- name: bad\n  preconditions: { runsOn: { ios: { os: "not a range" } } }\n'
        "  steps: [{ tap: { id: a } }]\n",
        encoding="utf-8",
    )
    lines = runs_summary(_eff("{}"), "xcuitest", "u", str(scenarios), _reader(_POOL))
    assert lines[1] == "  bad:"
    assert lines[2].startswith("    ✘ ")


def test_a_web_version_range_reads_the_engines_it_compares() -> None:
    eff = _eff('{ browser: { engine: webkit, version: ">=17" } }', "web", "{ url: 'http://x/' }")
    asked: list[list[str]] = []

    def read(_e: Effective, _a: str, _u: str, engines: list[str]) -> list[DeviceFacts]:
        asked.append(engines)
        return [DeviceFacts(udid="webkit", platform="web", model="webkit", release="18.0")]

    lines = runs_summary(eff, "playwright", "web-0", "", read_pool=read)
    assert asked == [["webkit"]]
    assert lines[2].startswith("    ✓ one run: ")

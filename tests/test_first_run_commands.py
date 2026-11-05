"""`record`, `crawl`, `repl`, and `audit` under `runsOn` conditions (BE-0452 unit 9).

The first three take the first run's device; `audit --repeat` forms runs as `run` does. Devices are
read through injected fakes, so no device CLI runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest
import typer

from bajutsu.analysis.audit import RepeatReport
from bajutsu.analysis.cli.audit import _audit_runs, _named
from bajutsu.cli._shared import _first_run_or_exit
from bajutsu.common.config import load_config, resolve, web_engine
from bajutsu.common.config.schema import DeviceProvider
from bajutsu.common.devices.facts import DeviceFacts
from bajutsu.common.scenario import Scenario

SE = "iPhone SE (3rd generation)"
POOL = [
    DeviceFacts("se17", "ios", SE, "17.5"),
    DeviceFacts("se18", "ios", SE, "18.2"),
    DeviceFacts("ipad18", "ios", "iPad Pro 13-inch (M4)", "18.2"),
]


@pytest.fixture(autouse=True)
def _identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Env:
        def resolve_device(self, udid: str) -> str:
            return "se18" if udid == "booted" else udid

    monkeypatch.setattr("bajutsu.common.platform_lifecycle.environment_for", lambda *a, **k: _Env())
    monkeypatch.setattr("bajutsu.analysis.cli.audit.environment_for", lambda *a, **k: _Env())


def _eff(platform: str, runs_on: str) -> Any:
    app = "{id: com.example.app}" if platform != "web" else "{url: 'http://localhost:1/'}"
    cfg = load_config(
        f"targets:\n  t:\n    platform: {platform}\n    subject: {app}\n    runsOn: {runs_on}\n"
    )
    return resolve(cfg, "t")


def _read(udids: Sequence[str]) -> list[DeviceFacts]:
    return [d for u in udids for d in POOL if d.udid == u]


def test_a_target_without_conditions_keeps_the_udid() -> None:
    eff = _eff("ios", "{}")
    assert _first_run_or_exit(eff, "xcuitest", "booted") == (eff, "booted")


def test_the_first_run_takes_the_first_model_at_the_newest_major_in_the_pool() -> None:
    eff = _eff("ios", f"{{ios: {{model: ['{SE}'], os: '>=17 <19'}}}}")
    _, udid = _first_run_or_exit(eff, "xcuitest", "se17,se18,ipad18", read_ios=_read)
    assert udid == "se18"


def test_no_device_fitting_the_first_run_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    eff = _eff("ios", "{ios: {os: '>=19'}}")
    with pytest.raises(typer.Exit) as exc:
        _first_run_or_exit(eff, "xcuitest", "se17,se18", read_ios=_read)
    assert exc.value.exit_code == 2
    assert 'os ">=19": no available device fits' in capsys.readouterr().out


def test_a_device_that_cannot_be_read_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    eff = _eff("android", "{android: {apiLevel: '34'}}")

    def broken(_serials: object) -> list[DeviceFacts]:
        from bajutsu.common.devices.facts import DeviceReadError

        raise DeviceReadError("emulator-5554 is an emulator but reports no AVD name")

    with pytest.raises(typer.Exit):
        _first_run_or_exit(eff, "adb", "emulator-5554", read_android=broken)
    assert "reports no AVD name" in capsys.readouterr().out


def test_a_web_engine_list_takes_the_first_engine() -> None:
    eff = _eff("web", "{browser: {kind: [webkit, chromium]}}")
    run_eff, udid = _first_run_or_exit(eff, "playwright", "booted")
    assert (web_engine(run_eff), udid, run_eff.conditions) == ("webkit", "booted", {})


def test_a_web_version_range_the_browser_misses_exits_2() -> None:
    eff = _eff("web", "{browser: {version: '>=999'}}")
    with pytest.raises(typer.Exit):
        _first_run_or_exit(
            eff,
            "playwright",
            "booted",
            read_browser=lambda e: DeviceFacts(f"web-{e}", "web", e, "130.0"),
        )


def test_a_grid_target_with_a_condition_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    eff = replace(
        _eff("ios", "{ios: {os: '18'}}"),
        device_provider=DeviceProvider(kind="appium", endpoint="https://grid.example/"),
    )
    with pytest.raises(typer.Exit):
        _first_run_or_exit(eff, "xcuitest", "https://grid.example/")
    assert "remote environment (appium)" in capsys.readouterr().out


def test_fake_takes_the_udid_as_given() -> None:
    eff = _eff("ios", "{ios: {os: '18'}}")
    assert _first_run_or_exit(eff, "fake", "booted") == (eff, "booted")


# --- audit --repeat ------------------------------------------------------------------------------


def _scenarios(count: int = 1) -> list[Scenario]:
    return [Scenario.model_validate({"name": f"s{i}", "steps": []}) for i in range(count)]


def test_audit_forms_one_run_per_major_on_its_own_device(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def audit(runs_on: str) -> tuple[list[tuple[Scenario, str, Any, list[str]]], bool]:
        return _audit_runs(
            _scenarios(),
            _eff("ios", runs_on),
            "xcuitest",
            ["se17"],
            "se17,se18",
            fake=False,
            read_facts=lambda lane: _read(list(lane.udids)),
        )

    runs, failed = audit("{ios: {os: '>=17'}}")
    assert [(s.name, label, udids) for s, label, _eff, udids in runs] == [
        ("s0", "iOS 17", ["se17"]),
        ("s0", "iOS 18", ["se18"]),
    ]
    assert not failed
    assert audit("{ios: {os: '>=19'}}") == ([], False)
    assert "not applicable: t/s0" in capsys.readouterr().out
    runs, failed = audit("{ios: {model: 'iPhone 99'}}")
    assert (runs, failed) == ([], True)
    assert 's0: no device fits this run: runsOn.ios.model  "iPhone 99"' in capsys.readouterr().out


def test_audit_without_conditions_keeps_one_run_per_scenario() -> None:
    eff = _eff("ios", "{}")
    runs, failed = _audit_runs(_scenarios(), eff, "xcuitest", ["se17"], "se17", fake=False)
    assert [(s.name, label, udids) for s, label, _e, udids in runs] == [("s0", "", ["se17"])]
    assert not failed


def test_audit_names_a_report_by_its_coordinates() -> None:
    report = RepeatReport("login", 2, deterministic=True)
    assert _named(report, "iOS 17").scenario == "login [iOS 17]"
    assert _named(report, "") is report


def test_audit_refuses_a_condition_on_a_grid_target(capsys: pytest.CaptureFixture[str]) -> None:
    eff = replace(
        _eff("ios", "{ios: {os: '18'}}"),
        device_provider=DeviceProvider(kind="appium", endpoint="https://grid.example/"),
    )
    with pytest.raises(typer.Exit) as exc:
        _audit_runs(_scenarios(), eff, "xcuitest", ["grid"], "grid", fake=False)
    assert exc.value.exit_code == 2
    assert "remote environment (appium)" in capsys.readouterr().out


def test_audit_exits_2_when_a_device_cannot_be_read(capsys: pytest.CaptureFixture[str]) -> None:
    from bajutsu.common.devices.facts import DeviceReadError

    def broken(_lane: object) -> list[DeviceFacts]:
        raise DeviceReadError("se17 is not an available Simulator")

    with pytest.raises(typer.Exit):
        _audit_runs(
            _scenarios(),
            _eff("ios", "{ios: {os: '18'}}"),
            "xcuitest",
            ["se17"],
            "se17",
            fake=False,
            read_facts=broken,
        )
    assert "not an available Simulator" in capsys.readouterr().out


def test_audit_runs_each_listed_engine_with_that_engine() -> None:
    eff = _eff("web", "{browser: {kind: [webkit, firefox]}}")
    runs, failed = _audit_runs(_scenarios(), eff, "playwright", ["web"], "booted", fake=False)
    assert [(label, web_engine(e), udids) for _s, label, e, udids in runs] == [
        ("webkit", "webkit", ["web"]),
        ("firefox", "firefox", ["web"]),
    ]
    assert not failed


def test_audit_run_pools_share_a_pool_per_device_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from bajutsu.analysis.cli.audit import _RunPools

    opened: list[list[str]] = []
    stopped: list[str] = []

    def pool(udids: list[str], *_a: object) -> tuple[object, object]:
        opened.append(udids)
        return f"lease-{udids[0]}", lambda: stopped.append(udids[0])

    monkeypatch.setattr("bajutsu.analysis.cli.audit.device_pool", pool)
    pools = _RunPools(["ios"], Path("runs"))
    eff = _eff("ios", "{}")
    leases: list[object] = [
        pools.lease_for(eff, ["se17"]),
        pools.lease_for(eff, ["se17"]),
        pools.lease_for(eff, ["se18"]),
    ]
    assert leases == ["lease-se17", "lease-se17", "lease-se18"]
    pools.shutdown()
    assert opened == [["se17"], ["se18"]]
    assert stopped == ["se17", "se18"]


def test_a_web_engine_list_beside_a_version_range_reads_only_the_first_engine() -> None:
    # The schema refuses this pair in one `runsOn`; built directly, the first run must still read
    # only the engine it takes rather than fail for the others.
    eff = _eff("web", "{browser: {kind: [webkit, chromium]}}")
    eff = replace(eff, conditions={**eff.conditions, "browser.version": ">=120"})
    read: list[str] = []

    def browser(engine: str) -> DeviceFacts:
        read.append(engine)
        return DeviceFacts(f"web-{engine}", "web", engine, "130.0")

    run_eff, udid = _first_run_or_exit(eff, "playwright", "booted", read_browser=browser)
    assert (web_engine(run_eff), udid, read) == ("webkit", "booted", ["webkit"])


def test_a_run_formation_value_error_exits_2(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: object, **_k: object) -> object:
        raise ValueError("no browser version was read for engine 'chromium'")

    monkeypatch.setattr("bajutsu.run.device_matrix.first_run", broken)
    eff = _eff("web", "{browser: {version: '>=120'}}")
    with pytest.raises(typer.Exit) as exc:
        _first_run_or_exit(
            eff,
            "playwright",
            "booted",
            read_browser=lambda e: DeviceFacts(f"web-{e}", "web", e, "130.0"),
        )
    assert exc.value.exit_code == 2
    assert "no browser version was read" in capsys.readouterr().out

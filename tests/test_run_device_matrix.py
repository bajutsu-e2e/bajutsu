"""`bajutsu run` forming runs from `runsOn` conditions and matching devices (BE-0452 unit 9).

No device is touched: the actuator selection, the device facts, the pool, and the run loop are
stubbed, and the host is pinned, so the run's own planning, outcomes, report, and exit code are what
these exercise.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import typer
from run_cli import invoke
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common.config import load_config, resolve
from bajutsu.common.config.schema import DeviceProvider
from bajutsu.common.devices.facts import DeviceFacts
from bajutsu.common.orchestrator import RunResult
from bajutsu.common.scenario import Scenario
from bajutsu.run.device_matrix import (
    TargetLanes,
    condition_problems,
    plan,
    read_pool_facts,
    render_not_applicable,
)

runner = CliRunner()

SE = "iPhone SE (3rd generation)"
IPAD = "iPad Pro 13-inch (M4)"
POOL = {
    "se17": DeviceFacts("se17", "ios", SE, "17.5", family="iPhone"),
    "se18": DeviceFacts("se18", "ios", SE, "18.2", family="iPhone"),
    "ipad17": DeviceFacts("ipad17", "ios", IPAD, "17.5", family="iPad"),
}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub every device touch point and record what the run asked of them."""
    seen: dict[str, Any] = {"pools": [], "runs": []}
    for module in ("bajutsu.cli._shared", "bajutsu.run.cli"):
        monkeypatch.setattr(f"{module}.current_host_os", lambda: "darwin")
    monkeypatch.setattr(
        "bajutsu.cli._shared.select_actuator",
        lambda backends: "fake" if backends == ["fake"] else "xcuitest",
    )

    class _Env:
        def resolve_device(self, udid: str) -> str:
            return udid

    monkeypatch.setattr("bajutsu.run.cli.environment_for", lambda *a, **k: _Env())
    monkeypatch.setattr(
        "bajutsu.run.device_matrix.facts.read_ios", lambda udids: [POOL[u] for u in udids]
    )

    def pool(udids: list[str], _backends: object, _eff: object, run_dir: Path, **_k: object):  # type: ignore[no-untyped-def]
        seen["pools"].append((list(udids), run_dir.name))
        return object(), lambda: None

    def run_all(eff: Any, scenarios: list[Scenario], _lease: object, **kw: Any) -> list[RunResult]:
        seen["runs"].append([s.name for s in scenarios])
        return [
            RunResult(scenario=s.name, ok=s.name != "breaks", steps=[], sid=f"{i:02d}-x")
            for i, s in enumerate(scenarios)
        ]

    monkeypatch.setattr("bajutsu.run.cli.device_pool", pool)
    monkeypatch.setattr("bajutsu.run.cli.run_all", run_all)
    return seen


def _args(tmp_path: Path, runs_on: str, scenarios: str, *extra: str) -> list[str]:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n  app:\n    platform: ios\n    app: {id: com.example.app}\n"
        f"    runsOn: {runs_on}\n",
        encoding="utf-8",
    )
    worker = tmp_path / "worker.yaml"
    worker.write_text("version: 1\ndrivers: [xcuitest, fake]\n", encoding="utf-8")
    file = tmp_path / "s.yaml"
    file.write_text(scenarios, encoding="utf-8")
    return [
        "run",
        "--target",
        "app",
        "--scenario",
        str(file),
        "--udid",
        "se17,se18,ipad17",
        "--worker-config",
        str(worker),
        "--config",
        str(cfg),
        "--runs-dir",
        str(tmp_path / "runs"),
        *extra,
    ]


def _manifest(tmp_path: Path) -> dict[str, Any]:
    (path,) = (tmp_path / "runs").glob("*/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_a_list_and_a_bounded_range_run_every_cell_on_its_own_devices(
    tmp_path: Path, harness: dict[str, Any]
) -> None:
    result = invoke(
        runner,
        app,
        _args(
            tmp_path,
            f"{{ios: {{model: ['{SE}', '{IPAD}'], os: '>=17 <19'}}}}",
            "- name: login\n  steps: []\n",
        ),
    )
    # iPad Pro on iOS 18 has no device, so that listed run fails and the invocation fails.
    assert result.exit_code == 1, result.output
    assert harness["pools"] == [(["se17"], "run-01"), (["se18"], "run-02"), (["ipad17"], "run-03")]
    assert "3 passed, 1 failed, 0 not applicable" in result.output
    manifest = _manifest(tmp_path)
    names = [(s["scenario"], s["coordinates"], s["ok"]) for s in manifest["scenarios"]]
    assert names == [
        ("login", f"{SE} / iOS 17", True),
        ("login", f"{SE} / iOS 18", True),
        ("login", f"{IPAD} / iOS 17", True),
        ("login", f"{IPAD} / iOS 18", False),
    ]
    assert manifest["scenarios"][0]["run_coordinates"] == {"app": {"model": SE, "os": "iOS 17"}}
    assert "no device fits this run" in manifest["scenarios"][3]["failure"]
    junit = next((tmp_path / "runs").glob("*/junit.xml")).read_text(encoding="utf-8")
    assert f'name="login [{IPAD} / iOS 18]"' in junit


def test_a_target_range_no_device_falls_in_is_not_applicable_beside_a_pass(
    tmp_path: Path, harness: dict[str, Any]
) -> None:
    # A scenario reaches a condition by naming a target that declares it (BE-0469).
    scenarios = (
        "- name: plain\n  targets: [app]\n  steps: []\n"
        "- name: future\n  targets: [later]\n  steps: []\n"
    )
    args = _args(tmp_path, "{ios: {os: '18'}}", scenarios)
    del args[1:3]  # each scenario names its own target
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + "  later:\n    platform: ios\n    app: {id: com.example.app}\n"
        "    runsOn: {ios: {os: '>=19'}}\n",
        encoding="utf-8",
    )
    result = invoke(runner, app, args)
    assert result.exit_code == 0, result.output
    assert "not applicable: later/future" in result.output
    assert '  runsOn.ios.os  ">=19"  available:' in result.output
    assert "1 passed, 0 failed, 1 not applicable" in result.output
    manifest = _manifest(tmp_path)
    assert manifest["ok"] is True
    assert [r["scenario"] for r in manifest["notApplicable"]] == ["future"]
    assert harness["runs"] == [["plain"]]


def test_an_invocation_with_every_record_not_applicable_exits_1(
    tmp_path: Path, harness: dict[str, Any]
) -> None:
    result = invoke(runner, app, _args(tmp_path, "{ios: {os: '>=19'}}", "- name: s\n  steps: []\n"))
    assert result.exit_code == 1, result.output
    assert "no run executed" in result.output
    assert harness["runs"] == []
    assert _manifest(tmp_path)["ok"] is False


def test_a_failed_scenario_in_a_cell_fails_the_run(tmp_path: Path, harness: dict[str, Any]) -> None:
    scenarios = "- name: works\n  steps: []\n- name: breaks\n  steps: []\n"
    result = invoke(
        runner, app, _args(tmp_path, f"{{ios: {{model: '{SE}', os: '17'}}}}", scenarios)
    )
    assert result.exit_code == 1, result.output
    # Both scenarios take the same eligible set, so they share one cell and one pool.
    assert harness["runs"] == [["works", "breaks"]]
    assert harness["pools"] == [(["se17"], "run-01")]


def test_fake_backend_evaluates_no_condition(
    tmp_path: Path, harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def single_path(*_a: object, **_k: object) -> object:
        raise typer.Exit(7)

    monkeypatch.setattr("bajutsu.run.cli.run_and_report", single_path)
    result = invoke(
        runner,
        app,
        _args(tmp_path, "{ios: {os: '>=19'}}", "- name: s\n  steps: []\n", "--backend", "fake"),
    )
    # No run is formed: the whole pool goes to today's single-engine path.
    assert result.exit_code == 7, result.output
    assert [udids for udids, _dir in harness["pools"]] == [["se17", "se18", "ipad17"]]
    assert "not yet supported" not in result.output


def test_browsers_beside_a_version_range_exits_2(tmp_path: Path) -> None:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n  site:\n    platform: web\n    app: {url: 'http://localhost:1/'}\n"
        "    runsOn: {browser: {version: '>=120'}}\n",
        encoding="utf-8",
    )
    file = tmp_path / "s.yaml"
    file.write_text("- name: s\n  steps: []\n", encoding="utf-8")
    result = invoke(
        runner,
        app,
        [
            "run",
            "--target",
            "site",
            "--scenario",
            str(file),
            "--browsers",
            "chromium,webkit",
            "--config",
            str(cfg),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--browsers forms an engine list" in result.output


# --- planning, without the command line ----------------------------------------------------------


def _eff(runs_on: str = "{}") -> Any:
    cfg = load_config(
        "targets:\n  app:\n    platform: ios\n    app: {id: com.example.app}\n"
        f"    runsOn: {runs_on}\n"
    )
    return resolve(cfg, "app")


def _scenario(body: str) -> Scenario:
    import yaml

    return Scenario.model_validate({"name": "s", "steps": [], **yaml.safe_load(body)})


def test_conditions_on_a_grid_target_fail_naming_the_field() -> None:
    grid = replace(
        _eff("{ios: {os: '>=18'}}"),
        device_provider=DeviceProvider(kind="appium", endpoint="https://grid.example/"),
    )
    problems = condition_problems([_scenario("{}")], {"app": grid}, "app", engine_flag=False)
    assert problems == [
        "s: runsOn.ios.os: target 'app' runs on a remote environment (appium), which has no "
        "device pool to match a condition against; remove the condition for this target"
    ]
    assert condition_problems([_scenario("{}")], {"app": _eff()}, "app", engine_flag=False) == []


def test_a_device_group_whose_members_disagree_fails() -> None:
    effs = {"a": replace(_eff("{ios: {os: '18'}}"), target="a"), "b": replace(_eff(), target="b")}
    problems = condition_problems([_scenario("{targets: [[a, b]]}")], effs, "a", engine_flag=False)
    assert problems == [
        "s: the device group a, b shares one device, so its targets' runsOn conditions must agree"
    ]


def test_plan_reads_each_pool_once_and_skips_failed_scenarios() -> None:
    reads: list[str] = []

    def read(lane: TargetLanes) -> list[DeviceFacts]:
        reads.append(lane.name)
        return [POOL[u] for u in lane.udids]

    lanes = {"app": TargetLanes("app", _eff("{ios: {os: '>=17'}}"), "xcuitest", tuple(POOL))}
    matrix = plan(
        [_scenario("{}"), _scenario("{}"), _scenario("{}")],
        lanes,
        "app",
        engine_flag=False,
        read_facts=read,
        read_browser=lambda engine: DeviceFacts("w", "web", engine, "1"),
        skip={1},
    )
    assert reads == ["app"]
    assert [r.scenario for r in matrix.records] == [0, 0, 2, 2]
    assert matrix.runnable == 4
    assert [cell.devices for cell in matrix.cells] == [{"app": ("se17",)}, {"app": ("se18",)}]


def test_render_not_applicable_names_the_target_and_scenario() -> None:
    record = {"scenario": "s", "target": "app", "coordinates": "", "reason": "runsOn.ios.os ..."}
    assert render_not_applicable(record) == "not applicable: app/s\n  runsOn.ios.os ..."


def test_plan_reads_each_compared_browser_once_and_narrows_by_its_version() -> None:
    cfg = load_config(
        "targets:\n  site:\n    platform: web\n    app: {url: 'http://localhost:1/'}\n"
        "    runsOn: {browser: {version: '>=120'}}\n"
        "  old:\n    platform: web\n    app: {url: 'http://localhost:1/'}\n"
        "    runsOn: {browser: {version: '<100'}}\n"
    )
    lanes = {
        name: TargetLanes(name, resolve(cfg, name), "playwright", ("web-0",))
        for name in ("site", "old")
    }
    reads: list[str] = []

    def browser(engine: str) -> DeviceFacts:
        reads.append(engine)
        return DeviceFacts(f"web-{engine}", "web", engine, "130.0.6723.31")

    matrix = plan(
        [
            _scenario("{}"),
            _scenario("{targets: [old]}"),
        ],
        lanes,
        "site",
        engine_flag=False,
        read_facts=lambda lane: [],
        read_browser=browser,
    )
    assert reads == ["chromium"]
    assert [r.combination.outcome for r in matrix.records] == ["run", "not-applicable"]
    assert matrix.cells[0].engines == {"site": "chromium"}


def test_read_pool_facts_refuses_a_platform_with_no_device() -> None:
    from bajutsu.common.devices.facts import DeviceReadError
    from bajutsu.run.device_matrix import read_pool_facts

    cfg = load_config("targets:\n  site:\n    platform: web\n    app: {url: 'http://x/'}\n")
    with pytest.raises(DeviceReadError, match="has no device"):
        read_pool_facts(TargetLanes("site", resolve(cfg, "site"), "playwright", ()))


def test_a_web_engine_list_runs_each_engine_as_its_own_cell(
    tmp_path: Path, harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.common.config import web_engine

    monkeypatch.setattr("bajutsu.cli._shared.select_actuator", lambda backends: "playwright")
    monkeypatch.setattr("bajutsu.cli._shared.ensure_web_runtime", lambda *a: None)
    installed: list[str] = []
    monkeypatch.setattr("bajutsu.run.cli.ensure_web_runtime", lambda _b, e: installed.append(e))
    engines: list[str] = []

    def pool(udids: list[str], _backends: object, eff: Any, run_dir: Path, **_k: object):  # type: ignore[no-untyped-def]
        engines.append(web_engine(eff))
        return object(), lambda: None

    monkeypatch.setattr("bajutsu.run.cli.device_pool", pool)
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n  site:\n    platform: web\n    app: {url: 'http://localhost:1/'}\n"
        "    runsOn: {browser: {kind: [webkit, firefox]}}\n",
        encoding="utf-8",
    )
    worker = tmp_path / "worker.yaml"
    worker.write_text("version: 1\ndrivers: [playwright]\n", encoding="utf-8")
    file = tmp_path / "s.yaml"
    file.write_text("- name: s\n  steps: []\n", encoding="utf-8")
    result = invoke(
        runner,
        app,
        [
            "run",
            "--target",
            "site",
            "--scenario",
            str(file),
            "--worker-config",
            str(worker),
            "--config",
            str(cfg),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert engines == installed == ["webkit", "firefox"]
    names = [s["coordinates"] for s in _manifest(tmp_path)["scenarios"]]
    assert names == ["webkit", "firefox"]


def test_unconditioned_scenarios_share_one_cell_whether_or_not_a_pool_was_read() -> None:
    lanes = {
        "app": TargetLanes("app", _eff(), "xcuitest", tuple(POOL)),
        "old": TargetLanes(
            "old", replace(_eff("{ios: {os: '17'}}"), target="old"), "xcuitest", tuple(POOL)
        ),
    }
    matrix = plan(
        [_scenario("{}"), _scenario("{targets: [old]}"), _scenario("{}")],
        lanes,
        "app",
        engine_flag=False,
        read_facts=lambda lane: [POOL[u] for u in lane.udids],
        read_browser=lambda engine: DeviceFacts("w", "web", engine, "1"),
    )
    assert [(cell.devices, [r.scenario for r in cell.runs]) for cell in matrix.cells] == [
        ({}, [0, 2]),
        ({"old": ("se17",)}, [1]),
    ]


def test_a_target_the_fake_driver_runs_reads_no_device() -> None:
    def never(_lane: TargetLanes) -> list[DeviceFacts]:
        raise AssertionError("a fake target's pool is never read")

    lanes = {"app": TargetLanes("app", _eff("{ios: {model: 'iPhone 99'}}"), "fake", ("booted",))}
    matrix = plan(
        [_scenario("{}")],
        lanes,
        "app",
        engine_flag=False,
        read_facts=never,
        read_browser=lambda engine: DeviceFacts("w", "web", engine, "1"),
    )
    assert [r.combination.outcome for r in matrix.records] == ["run"]
    assert matrix.cells[0].devices == {}


def test_installed_browser_installs_the_engine_before_reading_its_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bajutsu.common.devices.facts import DeviceReadError
    from bajutsu.run.device_matrix import installed_browser

    order: list[str] = []
    monkeypatch.setattr(
        "bajutsu.run.device_matrix.ensure_web_runtime", lambda _b, e: order.append(f"install {e}")
    )

    def read(engine: str) -> DeviceFacts:
        order.append(f"read {engine}")
        return DeviceFacts(f"web-{engine}", "web", engine, "18.2")

    assert installed_browser(["web"], read)("webkit").release == "18.2"
    assert order == ["install webkit", "read webkit"]

    def broken(_b: object, _e: str) -> None:
        raise RuntimeError("playwright install failed")

    monkeypatch.setattr("bajutsu.run.device_matrix.ensure_web_runtime", broken)
    with pytest.raises(DeviceReadError, match="could not install webkit"):
        installed_browser(["web"], read)("webkit")


def test_the_run_directory_is_owner_only_before_the_first_cell_writes(
    tmp_path: Path, harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    modes: list[int | None] = []

    def pool(udids: list[str], _backends: object, _eff: object, run_dir: Path, **_k: object):  # type: ignore[no-untyped-def]
        parent = run_dir.parent
        modes.append(parent.stat().st_mode & 0o777 if parent.exists() else None)
        return object(), lambda: None

    monkeypatch.setattr("bajutsu.run.cli.device_pool", pool)
    args = _args(tmp_path, "{ios: {os: '>=17 <19'}}", "- name: s\n  steps: []\n")
    result = invoke(runner, app, args)
    assert result.exit_code == 0, result.output
    assert modes and set(modes) == {0o700}


def test_two_web_pools_differing_only_by_engine_both_shut_down(
    tmp_path: Path, harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.common.config import web_engine

    monkeypatch.setattr("bajutsu.cli._shared.select_actuator", lambda backends: "playwright")
    monkeypatch.setattr("bajutsu.cli._shared.ensure_web_runtime", lambda *a: None)
    monkeypatch.setattr("bajutsu.run.cli.ensure_web_runtime", lambda *a: None)
    opened: list[str] = []
    closed: list[str] = []

    def pool(udids: list[str], _backends: object, eff: Any, run_dir: Path, **_k: object):  # type: ignore[no-untyped-def]
        engine = web_engine(eff)
        opened.append(engine)
        return object(), lambda: closed.append(engine)

    monkeypatch.setattr("bajutsu.run.cli.device_pool", pool)
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        "  site:\n    platform: web\n    app: {url: 'http://localhost:1/'}\n"
        "    runsOn: {browser: {kind: [chromium]}}\n"
        "  admin:\n    platform: web\n    app: {url: 'http://localhost:2/'}\n"
        "    runsOn: {browser: {kind: [webkit]}}\n",
        encoding="utf-8",
    )
    worker = tmp_path / "worker.yaml"
    worker.write_text("version: 1\ndrivers: [playwright]\n", encoding="utf-8")
    file = tmp_path / "s.yaml"
    file.write_text("- name: s\n  targets: [site, admin]\n  steps: []\n", encoding="utf-8")
    result = invoke(
        runner,
        app,
        [
            "run",
            "--target",
            "site",
            "--scenario",
            str(file),
            "--worker-config",
            str(worker),
            "--config",
            str(cfg),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--workers",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert sorted(opened) == sorted(closed) == ["chromium", "webkit"]


# --- BE-0468: a listed iOS kind forms a cell per kind, and each cell drives its own kind ---

_MIXED = {
    "sim": DeviceFacts("sim", "ios", "iPad Pro 13-inch (M4)", "18.2", family="iPad"),
    "real": DeviceFacts("real", "ios", "iPad Air", "18.1", family="iPad", physical=True),
}


def test_plan_puts_each_listed_kind_in_its_own_cell() -> None:
    eff = _eff("{ios: {kind: [simulator, device], family: ipad}}")
    lanes = {"app": TargetLanes("app", eff, "xcuitest", tuple(_MIXED))}
    matrix = plan(
        [_scenario("{}")],
        lanes,
        "app",
        engine_flag=False,
        read_facts=lambda lane: [_MIXED[u] for u in lane.udids],
        read_browser=lambda engine: DeviceFacts("w", "web", engine, "1"),
    )
    assert [(c.devices, c.kinds) for c in matrix.cells] == [
        ({"app": ("sim",)}, {"app": "simulator"}),
        ({"app": ("real",)}, {"app": "device"}),
    ]
    assert [r.combination.label for r in matrix.records] == ["simulator", "device"]


def test_a_two_kind_target_reads_a_mixed_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    def pool(udids: list[str]) -> list[DeviceFacts]:
        seen.append(tuple(udids))
        return [_MIXED[u] for u in udids]

    monkeypatch.setattr("bajutsu.run.device_matrix.facts.read_ios_pool", pool)
    eff = _eff("{ios: {kind: [simulator, device]}}")
    assert read_pool_facts(TargetLanes("app", eff, "xcuitest", ("sim", "real"))) == list(
        _MIXED.values()
    )
    assert seen == [("sim", "real")]


def test_each_kind_of_a_run_reaches_its_pool(
    tmp_path: Path, harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.common.config.accessors import xcuitest_targets_real_device

    monkeypatch.setattr(
        "bajutsu.run.device_matrix.facts.read_ios_pool", lambda udids: [_MIXED[u] for u in udids]
    )
    kinds: list[tuple[list[str], bool]] = []

    def pool(udids: list[str], _backends: object, eff: Any, run_dir: Path, **_k: object):  # type: ignore[no-untyped-def]
        kinds.append((list(udids), xcuitest_targets_real_device(eff)))
        return object(), lambda: None

    monkeypatch.setattr("bajutsu.run.cli.device_pool", pool)
    args = _args(tmp_path, "{ios: {kind: [simulator, device]}}", "- name: s\n  steps: []\n")
    args[args.index("--udid") + 1] = "sim,real"
    result = invoke(runner, app, args)
    assert result.exit_code == 0, result.output
    assert kinds == [(["sim"], False), (["real"], True)]
    coordinates = [s["run_coordinates"] for s in _manifest(tmp_path)["scenarios"]]
    assert coordinates == [{"app": {"kind": "simulator"}}, {"app": {"kind": "device"}}]


@pytest.mark.parametrize(
    ("family", "label", "udids"),
    [
        ("[ipad, iphone]", "ipad", ["ipad17"]),
        ("[iphone, ipad]", "iphone", ["se18"]),  # the newest release of the first model
        ("ipad", "", ["ipad17"]),
    ],
)
def test_a_family_list_names_its_winner_in_the_label_and_the_manifest(
    tmp_path: Path, harness: dict[str, Any], family: str, label: str, udids: list[str]
) -> None:
    # BE-0468: the winner of two or more listed families is a coordinate; a single family is not.
    result = invoke(
        runner, app, _args(tmp_path, f"{{ios: {{family: {family}}}}}", "- name: s\n  steps: []\n")
    )
    assert result.exit_code == 0, result.output
    assert [u for u, _run in harness["pools"]] == [udids]
    (scenario,) = _manifest(tmp_path)["scenarios"]
    assert scenario["coordinates"] == label
    assert scenario.get("run_coordinates", {}) == ({"app": {"family": label}} if label else {})


def test_an_ios_pool_reads_through_the_listing_its_kinds_need(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bajutsu.run.device_matrix import read_ios_facts

    calls: list[str] = []

    def reader(name: str):  # type: ignore[no-untyped-def]
        def read(_udids: object) -> list[DeviceFacts]:
            calls.append(name)
            return []

        return read

    for name in ("read_ios", "read_ios_device", "read_ios_pool"):
        monkeypatch.setattr(f"bajutsu.run.device_matrix.facts.{name}", reader(name))
    read_ios_facts(_eff(), ["u"])
    read_ios_facts(_eff("{ios: {kind: device}}"), ["u"])
    read_ios_facts(_eff("{ios: {kind: [simulator, device]}}"), ["u"])
    assert calls == ["read_ios", "read_ios_device", "read_ios_pool"]


def test_the_first_run_of_a_kind_list_drives_its_kind() -> None:
    from bajutsu.common.config.accessors import xcuitest_targets_real_device
    from bajutsu.run.device_matrix import first_run_device

    eff = _eff("{ios: {kind: [device, simulator]}}")
    run_eff, udid = first_run_device(
        eff, "xcuitest", "sim,real", read_ios=lambda udids: [_MIXED[u] for u in udids]
    )
    assert udid == "real"
    assert xcuitest_targets_real_device(run_eff)
    assert "kind" not in run_eff.conditions


@pytest.mark.parametrize("order", [("sim", "app"), ("app", "sim")])
def test_a_shared_pool_reads_one_listing_whatever_target_comes_first(
    order: tuple[str, str],
) -> None:
    # Two targets on one pool: the listing and each target's devices never depend on scenario order.
    cfg = load_config(
        "targets:\n"
        "  app:\n    platform: ios\n    app: {id: com.example.app}\n"
        "    runsOn: {ios: {kind: [simulator, device]}}\n"
        "  sim:\n    platform: ios\n    app: {id: com.example.app}\n"
        "    runsOn: {ios: {family: ipad}}\n"
    )
    lanes = {n: TargetLanes(n, resolve(cfg, n), "xcuitest", tuple(_MIXED)) for n in ("app", "sim")}
    read: list[bool] = []

    def facts_of(lane: TargetLanes) -> list[DeviceFacts]:
        read.append("kind" in lane.eff.conditions)
        return list(_MIXED.values())

    matrix = plan(
        [_scenario(f"{{targets: [{name}]}}") for name in order],
        lanes,
        "app",
        engine_flag=False,
        read_facts=facts_of,
        read_browser=lambda engine: DeviceFacts("w", "web", engine, "1"),
    )
    assert read == [True]  # the mixed listing, read once
    sim_cells = [c.devices for c in matrix.cells if "sim" in c.devices]
    assert sim_cells == [{"sim": ("sim",)}]  # the Simulator-only target never takes the real device

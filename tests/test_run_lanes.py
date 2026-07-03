"""Lane resolution for `bajutsu run` — how --udid / --workers map to a device pool,
plus the run command's directory-resolution and file-expansion helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu.cli.commands.run import (
    _expand_file,
    _resolve_baselines_dir,
    _resolve_goldens_dir,
    _resolve_lanes,
    _resolve_schemas_dir,
)
from bajutsu.config import Effective, load_config, resolve


def _resolve(udid: str) -> str:
    # Stand-in for env.resolve_udid: echo back a concrete udid per token.
    return f"resolved:{udid}"


def test_web_workers_become_parallel_lanes() -> None:
    # Web has no simctl udid; --workers N alone is N near-free BrowserContext lanes.
    udids, workers = _resolve_lanes("playwright", udid="booted", workers=3, resolve_udid=_resolve)
    assert udids == ["web-0", "web-1", "web-2"]
    assert workers == 3


def test_web_defaults_to_a_single_lane() -> None:
    udids, workers = _resolve_lanes("playwright", udid="booted", workers=1, resolve_udid=_resolve)
    assert udids == ["web-0"]
    assert workers == 1


def test_web_workers_floored_at_one() -> None:
    udids, workers = _resolve_lanes("playwright", udid="booted", workers=0, resolve_udid=_resolve)
    assert udids == ["web-0"]
    assert workers == 1


def test_idb_resolves_each_udid_in_the_comma_list() -> None:
    udids, workers = _resolve_lanes("idb", udid="A, B ,C", workers=5, resolve_udid=_resolve)
    assert udids == ["resolved:A", "resolved:B", "resolved:C"]
    # workers is capped to the pool size (3 devices), even though 5 were requested.
    assert workers == 3


def test_idb_single_device_is_serial() -> None:
    udids, workers = _resolve_lanes("idb", udid="only", workers=4, resolve_udid=_resolve)
    assert udids == ["resolved:only"]
    assert workers == 1


def _eff(**target: str) -> Effective:
    # A minimal iOS-shaped target; pass baselines/schemas/goldens/setup to fill those config fields.
    fields = "".join(f"    {k}: {v}\n" for k, v in target.items())
    cfg = load_config(f"targets:\n  x:\n    bundleId: com.x\n{fields}")
    return resolve(cfg, "x")


# --- directory resolution: --flag > config > default beside the scenario (BE-0006 / visual / schema)


def test_baselines_dir_flag_wins() -> None:
    got = _resolve_baselines_dir("cli/dir", _eff(baselines="cfg/dir"), Path("e2e/s.yaml"))
    assert got == Path("cli/dir")


def test_baselines_dir_config_when_no_flag() -> None:
    got = _resolve_baselines_dir("", _eff(baselines="cfg/dir"), Path("e2e/s.yaml"))
    assert got == Path("cfg/dir")


def test_baselines_dir_defaults_beside_the_scenario() -> None:
    got = _resolve_baselines_dir("", _eff(), Path("e2e/s.yaml"))
    assert got == Path("e2e/baselines")


def test_schemas_dir_flag_config_default() -> None:
    scn = Path("e2e/s.yaml")
    assert _resolve_schemas_dir("cli/dir", _eff(schemas="cfg/dir"), scn) == Path("cli/dir")
    assert _resolve_schemas_dir("", _eff(schemas="cfg/dir"), scn) == Path("cfg/dir")
    assert _resolve_schemas_dir("", _eff(), scn) == Path("e2e/schemas")


def test_goldens_dir_flag_config_default() -> None:
    scn = Path("e2e/s.yaml")
    assert _resolve_goldens_dir("cli/dir", _eff(goldens="cfg/dir"), scn) == Path("cli/dir")
    assert _resolve_goldens_dir("", _eff(goldens="cfg/dir"), scn) == Path("cfg/dir")
    assert _resolve_goldens_dir("", _eff(), scn) == Path("e2e/goldens")


# --- _expand_file: loads one file and resolves its setup/component/data refs relative to its dir


def test_expand_file_returns_scenarios_and_file_description(tmp_path: Path) -> None:
    # The `{description, scenarios}` mapping form: the file-level description rides back alongside
    # the expanded scenarios, and a ref-free file passes through unchanged.
    path = tmp_path / "s.yaml"
    path.write_text(
        "description: a suite\nscenarios:\n  - name: demo\n    steps:\n      - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    scenarios, description = _expand_file(path, _eff())
    assert description == "a suite"
    assert [s.name for s in scenarios] == ["demo"]


def test_expand_file_missing_setup_ref_exits_2(tmp_path: Path) -> None:
    # A setup prelude resolved relative to the file's dir; a missing file is a usage error (exit 2).
    path = tmp_path / "s.yaml"
    path.write_text("- name: demo\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff(setup="missing.yaml"))
    assert exc.value.exit_code == 2


def test_expand_file_missing_component_ref_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  steps:\n    - use: { component: missing.yaml }\n", encoding="utf-8"
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff())
    assert exc.value.exit_code == 2


def test_expand_file_missing_data_file_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  dataFile: missing.csv\n  steps:\n    - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff())
    assert exc.value.exit_code == 2

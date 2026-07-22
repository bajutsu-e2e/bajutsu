"""Lane resolution for `bajutsu run` — how --udid / --workers map to a device pool,
plus the run command's directory-resolution and file-expansion helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu.cli.commands.run import (
    _alert_guard_factory,
    _expand_file,
    _filter_scenarios,
    _load_scenarios,
    _resolve_dir,
    _resolve_evidence_dirs,
    _resolve_lanes,
    _resolve_network,
    _resolve_secrets,
)
from bajutsu.config import Effective, load_config, resolve
from bajutsu.scenario import load_scenarios


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


def test_xcuitest_resolves_each_udid_in_the_comma_list() -> None:
    udids, workers = _resolve_lanes("xcuitest", udid="A, B ,C", workers=5, resolve_udid=_resolve)
    assert udids == ["resolved:A", "resolved:B", "resolved:C"]
    # workers is capped to the pool size (3 devices), even though 5 were requested.
    assert workers == 3


def test_xcuitest_single_device_is_serial() -> None:
    udids, workers = _resolve_lanes("xcuitest", udid="only", workers=4, resolve_udid=_resolve)
    assert udids == ["resolved:only"]
    assert workers == 1


def _eff(**target: str) -> Effective:
    # A minimal iOS-shaped target; pass baselines/schemas/goldens/setup to fill those config fields.
    fields = "".join(f"    {k}: {v}\n" for k, v in target.items())
    cfg = load_config(f"targets:\n  x:\n    bundleId: com.x\n{fields}")
    return resolve(cfg, "x")


# --- directory resolution: --flag > config > default beside the scenario (BE-0006 / visual / schema)


def test_baselines_dir_flag_wins() -> None:
    got = _resolve_dir(
        "cli/dir",
        _eff(baselines="cfg/dir").evidence_dirs.baselines,
        Path("e2e/s.yaml"),
        "baselines",
    )
    assert got == Path("cli/dir")


def test_baselines_dir_config_when_no_flag() -> None:
    got = _resolve_dir(
        "", _eff(baselines="cfg/dir").evidence_dirs.baselines, Path("e2e/s.yaml"), "baselines"
    )
    assert got == Path("cfg/dir")


def test_baselines_dir_defaults_beside_the_scenario() -> None:
    got = _resolve_dir("", _eff().evidence_dirs.baselines, Path("e2e/s.yaml"), "baselines")
    assert got == Path("e2e/baselines")


def test_schemas_dir_flag_config_default() -> None:
    scn = Path("e2e/s.yaml")
    assert _resolve_dir(
        "cli/dir", _eff(schemas="cfg/dir").evidence_dirs.schemas, scn, "schemas"
    ) == Path("cli/dir")
    assert _resolve_dir("", _eff(schemas="cfg/dir").evidence_dirs.schemas, scn, "schemas") == Path(
        "cfg/dir"
    )
    assert _resolve_dir("", _eff().evidence_dirs.schemas, scn, "schemas") == Path("e2e/schemas")


def test_goldens_dir_flag_config_default() -> None:
    scn = Path("e2e/s.yaml")
    assert _resolve_dir(
        "cli/dir", _eff(goldens="cfg/dir").evidence_dirs.goldens, scn, "goldens"
    ) == Path("cli/dir")
    assert _resolve_dir("", _eff(goldens="cfg/dir").evidence_dirs.goldens, scn, "goldens") == Path(
        "cfg/dir"
    )
    assert _resolve_dir("", _eff().evidence_dirs.goldens, scn, "goldens") == Path("e2e/goldens")


# --- _expand_file: loads one file and resolves its setup/component/data refs relative to its dir


def test_expand_file_returns_scenarios_and_file_description(tmp_path: Path) -> None:
    # The `{description, scenarios}` mapping form: the file-level description rides back alongside
    # the expanded scenarios, and a ref-free file passes through unchanged.
    path = tmp_path / "s.yaml"
    path.write_text(
        "description: a suite\nscenarios:\n  - name: demo\n    steps:\n      - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    scenarios, description = _expand_file(path, _eff(), root=tmp_path)
    assert description == "a suite"
    assert [s.name for s in scenarios] == ["demo"]


def test_expand_file_missing_setup_ref_exits_2(tmp_path: Path) -> None:
    # A setup prelude resolved relative to the file's dir; a missing file is a usage error (exit 2).
    path = tmp_path / "s.yaml"
    path.write_text("- name: demo\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff(setup="missing.yaml"), root=tmp_path)
    assert exc.value.exit_code == 2


def test_expand_file_missing_component_ref_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  steps:\n    - use: { component: missing.yaml }\n", encoding="utf-8"
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff(), root=tmp_path)
    assert exc.value.exit_code == 2


def test_expand_file_missing_data_file_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  dataFile: missing.csv\n  steps:\n    - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff(), root=tmp_path)
    assert exc.value.exit_code == 2


# --- _resolve_secrets: bind declared secrets present in the environment (never the absent ones)


def test_resolve_secrets_binds_only_present_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN", "s3cr3t")
    monkeypatch.delenv("ABSENT", raising=False)
    bindings, values = _resolve_secrets(_eff(secrets="[TOKEN, ABSENT]"))
    assert bindings == {"secrets.TOKEN": "s3cr3t"}  # ABSENT is unbound, not an empty string
    assert values == ["s3cr3t"]


# --- _load_scenarios: the --scenario file, or the target's configured dir


def test_load_scenarios_single_file(tmp_path: Path) -> None:
    scn = tmp_path / "s.yaml"
    scn.write_text(
        "description: suite\nscenarios:\n  - name: demo\n    steps:\n      - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    scenarios, description, source_name, files = _load_scenarios(_eff(), str(scn), "x")
    assert [s.name for s in scenarios] == ["demo"]
    assert description == "suite"  # the single file's description rides back
    assert source_name == "s.yaml"
    assert files == [scn]


# --- _filter_scenarios: --tag/--exclude selection plus the --erase override


def _one_scenario(name: str, *, tags: str = "") -> str:
    tag_line = f"  tags: {tags}\n" if tags else ""
    return f"- name: {name}\n{tag_line}  steps:\n    - tap: {{ id: home.title }}\n"


def test_filter_scenarios_selects_by_tag() -> None:
    scenarios = load_scenarios(
        _one_scenario("a", tags="[smoke]") + _one_scenario("b", tags="[slow]")
    )
    got = _filter_scenarios(scenarios, "smoke", "", None, False)
    assert [s.name for s in got] == ["a"]


def test_filter_scenarios_no_match_exits_2() -> None:
    scenarios = load_scenarios(_one_scenario("a", tags="[smoke]"))
    with pytest.raises(typer.Exit) as exc:
        _filter_scenarios(scenarios, "nightly", "", None, False)
    assert exc.value.exit_code == 2


def test_filter_scenarios_erase_override() -> None:
    scenarios = load_scenarios(_one_scenario("a"))
    _filter_scenarios(scenarios, "", "", True, False)  # --erase forces every scenario on
    assert scenarios[0].preconditions.erase is True


def test_filter_scenarios_erase_inherits_target_default() -> None:
    # BE-0177: an unset scenario (erase None) inherits the target config default when no flag is given.
    scenarios = load_scenarios(_one_scenario("a"))
    assert scenarios[0].preconditions.erase is None
    _filter_scenarios(scenarios, "", "", None, True)
    assert scenarios[0].preconditions.erase is True


def test_filter_scenarios_erase_scenario_beats_target() -> None:
    # BE-0177: a scenario's explicit erase wins over the target default; the flag is unset here.
    scenarios = load_scenarios(_one_scenario("a") + "  preconditions:\n    erase: false\n")
    _filter_scenarios(scenarios, "", "", None, True)
    assert scenarios[0].preconditions.erase is False


def test_filter_scenarios_erase_flag_beats_scenario_and_target() -> None:
    # BE-0177: `--no-erase` overrides even a scenario that explicitly set erase: true.
    scenarios = load_scenarios(_one_scenario("a") + "  preconditions:\n    erase: true\n")
    _filter_scenarios(scenarios, "", "", False, True)
    assert scenarios[0].preconditions.erase is False


# --- _alert_guard_factory: build the guard factory, or None when no scenario wants one


def test_alert_guard_factory_none_when_all_disabled() -> None:
    scenarios = load_scenarios(
        "- name: a\n  dismissAlerts: false\n" + "  steps:\n    - tap: { id: home.title }\n"
    )
    assert _alert_guard_factory(scenarios, _eff(), "") is None


def test_alert_guard_factory_none_when_target_disables() -> None:
    # BE-0177: a scenario with no dismissAlerts inherits the target config's `dismissAlerts: false`,
    # so the factory builds no guard at all (the enabled bit resolves scenario > target > built-in on).
    scenarios = load_scenarios(_one_scenario("a"))
    assert _alert_guard_factory(scenarios, _eff(dismissAlerts="false"), "") is None


def test_alert_guard_factory_scenario_reenables_over_target() -> None:
    # BE-0177: a scenario's explicit `dismissAlerts: true` wins over the target's `false`, so a guard
    # is still built (it no-ops here only because the test env has no AI credential).
    scenarios = load_scenarios(
        "- name: a\n  dismissAlerts: true\n  steps:\n    - tap: { id: home.title }\n"
    )
    assert _alert_guard_factory(scenarios, _eff(dismissAlerts="false"), "") is not None


# --- _resolve_network: --network/--no-network flag > target `network` config > built-in on (BE-0177)


def test_resolve_network_flag_wins() -> None:
    assert _resolve_network(False, True) is False  # --no-network overrides a target `network: true`
    assert _resolve_network(True, False) is True  # --network overrides a target `network: false`


def test_resolve_network_falls_back_to_target_then_builtin() -> None:
    assert _resolve_network(None, False) is False  # no flag → the target's `network` config
    assert (
        _resolve_network(None, True) is True
    )  # no flag, target on (the resolve() built-in default)


def test_alert_guard_factory_noop_without_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    # Guard on by default, but with no AI credential the factory hands back a per-scenario guard
    # that no-ops (returns None) rather than a hosted fallback — the deterministic gate runs AI-free.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    scenarios = load_scenarios(_one_scenario("a"))
    factory = _alert_guard_factory(scenarios, _eff(), "")
    assert factory is not None
    assert factory(scenarios[0]) is None


# --- _resolve_evidence_dirs: baselines/schemas dirs plus the golden context (only when it exists)


def test_resolve_evidence_dirs_defaults_and_golden_context(tmp_path: Path) -> None:
    scn = tmp_path / "e2e" / "s.yaml"
    scn.parent.mkdir()
    baselines_dir, schemas_dir, gc = _resolve_evidence_dirs("", "", "", _eff(), scn)
    assert baselines_dir == scn.parent / "baselines"
    assert schemas_dir == scn.parent / "schemas"
    assert gc is None  # no goldens dir beside the scenario → no golden context
    (scn.parent / "goldens").mkdir()
    _, _, gc2 = _resolve_evidence_dirs("", "", "", _eff(), scn)
    assert gc2 is not None  # goldens dir exists → golden assertions can resolve within it

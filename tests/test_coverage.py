"""Tests for the static e2e coverage map (bajutsu/coverage.py, BE-0050).

Coverage is a pure, device-free, AI-free function of a scenario suite plus the app's declared
`idNamespaces`: it reports which declared namespaces the suite's selectors touch, which are gaps
(no scenario references them), and which referenced ids fall outside the declared namespaces. It
never runs a scenario and never decides pass/fail.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.coverage import coverage, render
from bajutsu.scenario import load_scenarios

runner = CliRunner()


def _cov(yaml: str, namespaces: list[str]):  # type: ignore[no-untyped-def]
    return coverage(load_scenarios(yaml), namespaces)


def test_referenced_namespace_is_covered_the_rest_are_gaps() -> None:
    cov = _cov(
        "- name: x\n  steps:\n"
        "    - tap: { id: home.start }\n"
        "  expect:\n"
        "    - exists: { id: home.title }\n",
        ["home", "auth", "cart"],
    )
    assert cov.total == 3 and cov.covered == 1
    assert cov.coverage == 1 / 3
    assert cov.gaps == ["auth", "cart"]
    [ns] = cov.namespaces
    assert ns.namespace == "home"
    assert ns.ids == ["home.start", "home.title"]  # sorted, deduped
    assert cov.off_namespace == []


def test_referenced_id_outside_declared_namespaces_is_off_namespace() -> None:
    cov = _cov(
        "- name: x\n  steps:\n    - tap: { id: legacy.button }\n",
        ["home"],
    )
    assert cov.covered == 0
    assert cov.gaps == ["home"]
    assert cov.off_namespace == ["legacy.button"]


def test_idmatches_counts_toward_its_namespace() -> None:
    cov = _cov(
        "- name: x\n  steps:\n    - tap: { idMatches: 'home.item-*' }\n",
        ["home"],
    )
    assert cov.covered == 1
    [ns] = cov.namespaces
    assert ns.ids == ["home.item-*"]


def test_ids_aggregate_across_scenarios_and_dedupe() -> None:
    cov = _cov(
        "- name: a\n  steps:\n    - tap: { id: home.start }\n"
        "- name: b\n  steps:\n    - tap: { id: home.start }\n    - tap: { id: home.menu }\n",
        ["home"],
    )
    [ns] = cov.namespaces
    assert ns.ids == ["home.menu", "home.start"]


def test_nested_within_and_control_flow_selectors_counted() -> None:
    cov = _cov(
        "- name: x\n  steps:\n"
        "    - tap: { id: cart.item, within: { id: cart.list } }\n"
        "    - forEach: { sel: { idMatches: 'cart.row-*' }, as: r, "
        "steps: [ { tap: { id: cart.remove } } ] }\n",
        ["cart"],
    )
    [ns] = cov.namespaces
    assert ns.ids == ["cart.item", "cart.list", "cart.remove", "cart.row-*"]


def test_no_declared_namespaces_is_full_coverage() -> None:
    cov = _cov("- name: x\n  steps:\n    - tap: { id: home.start }\n", [])
    assert cov.total == 0 and cov.covered == 0
    assert cov.coverage == 1.0
    assert cov.gaps == []


def test_selectors_without_an_id_do_not_count() -> None:
    cov = _cov(
        "- name: x\n  steps:\n    - tap: { label: Start }\n",
        ["home"],
    )
    assert cov.covered == 0
    assert cov.gaps == ["home"]
    assert cov.off_namespace == []


def test_render_reports_coverage_gaps_and_off_namespace() -> None:
    cov = _cov(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n    - tap: { id: legacy.x }\n",
        ["home", "auth"],
    )
    text = render(cov)
    assert "coverage: 0.50 (1/2)" in text
    assert "home" in text and "home.start" in text
    assert "gaps" in text and "auth" in text
    assert "off-namespace" in text and "legacy.x" in text


def test_cli_reports_coverage_for_an_app(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "apps:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home, auth]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--app", "demo", "--config", str(config)])
    assert result.exit_code == 0  # read-only: never gates
    assert "coverage: 0.50 (1/2)" in result.stdout
    assert "auth" in result.stdout  # the gap


def test_cli_json_output(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "apps:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home, auth]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--app", "demo", "--config", str(config), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["total"] == 2 and data["covered"] == 1
    assert data["gaps"] == ["auth"]
    assert data["namespaces"][0]["namespace"] == "home"


def test_cli_app_without_scenarios_dir_exits_2(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "apps:\n  demo:\n    bundleId: com.example.demo\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--app", "demo", "--config", str(config)])
    assert result.exit_code == 2

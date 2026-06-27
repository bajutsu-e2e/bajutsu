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
from bajutsu.coverage import (
    ScreenRef,
    coverage,
    endpoint_coverage,
    observed_id_coverage,
    referenced_requests,
    render,
    render_html,
    render_observed_ids,
    render_screens,
    screen_coverage,
)
from bajutsu.crawl import fingerprint as screen_fingerprint
from bajutsu.network import NetworkExchange
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
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home, auth]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--target", "demo", "--config", str(config)])
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
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home, auth]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--target", "demo", "--config", str(config), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["total"] == 2 and data["covered"] == 1
    assert data["gaps"] == ["auth"]
    assert data["namespaces"][0]["namespace"] == "home"


def test_cli_app_without_scenarios_dir_exits_2(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--target", "demo", "--config", str(config)])
    assert result.exit_code == 2


# --- BE-0050: endpoint coverage (observed network.json vs declared assertion matchers) ---


def _ex(method: str, path: str) -> NetworkExchange:
    return NetworkExchange(method=method, path=path)


def test_referenced_requests_collects_from_all_network_assertions() -> None:
    [scn] = load_scenarios(
        "- name: x\n  steps:\n"
        "    - assert: [ { request: { method: GET, path: /a } } ]\n"
        "    - assert: [ { event: { path: /track } } ]\n"
        "  expect:\n"
        "    - requestSequence:\n"
        "        - { method: POST, path: /b }\n"
        "        - { path: /c }\n"
    )
    reqs = referenced_requests(scn)
    paths = sorted(r.path for r in reqs if r.path is not None)
    assert paths == ["/a", "/b", "/c", "/track"]


def test_referenced_requests_includes_wait_until_request() -> None:
    # `wait: { until: { request: ... } }` declares an endpoint too (same RequestMatch model).
    [scn] = load_scenarios(
        "- name: x\n  steps:\n    - wait: { until: { request: { path: /ready } }, timeout: 5 }\n"
    )
    assert [r.path for r in referenced_requests(scn)] == ["/ready"]
    ec = endpoint_coverage([scn], [_ex("GET", "/ready")])
    assert ec.asserted == ["GET /ready"] and ec.unasserted == []


def test_endpoint_coverage_asserted_and_unasserted() -> None:
    [scn] = load_scenarios("- name: x\n  steps:\n    - assert: [ { request: { path: /a } } ]\n")
    observed = [_ex("GET", "/a"), _ex("POST", "/b")]
    ec = endpoint_coverage([scn], observed)
    assert ec.observed == ["GET /a", "POST /b"]
    assert ec.asserted == ["GET /a"]
    assert ec.unasserted == ["POST /b"]  # observed traffic the suite never asserts on
    assert ec.coverage == 0.5
    assert ec.declared_unobserved == []


def test_endpoint_coverage_declared_unobserved() -> None:
    [scn] = load_scenarios("- name: x\n  steps:\n    - assert: [ { request: { path: /never } } ]\n")
    ec = endpoint_coverage([scn], [_ex("GET", "/a")])
    assert ec.unasserted == ["GET /a"]
    assert ec.coverage == 0.0
    assert any("/never" in d for d in ec.declared_unobserved)


def test_endpoint_coverage_no_observed_is_full() -> None:
    [scn] = load_scenarios("- name: x\n  steps:\n    - assert: [ { request: { path: /a } } ]\n")
    ec = endpoint_coverage([scn], [])
    assert ec.observed == [] and ec.coverage == 1.0


def test_endpoint_coverage_many_matchers_and_exchanges() -> None:
    # Exercises the full matcher-vs-exchange relation in one shot: a matcher that matches (path /a),
    # one that matches via regex (pathMatches /b), one that matches nothing (/never), an exchange
    # asserted by a matcher, and an exchange (/c) no matcher covers.
    [scn] = load_scenarios(
        "- name: x\n  steps:\n    - assert:\n"
        "        - { request: { path: /a } }\n"
        "        - { request: { pathMatches: /b } }\n"
        "        - { request: { path: /never } }\n"
    )
    ec = endpoint_coverage([scn], [_ex("GET", "/a"), _ex("POST", "/b"), _ex("GET", "/c")])
    assert ec.observed == ["GET /a", "GET /c", "POST /b"]
    assert ec.asserted == ["GET /a", "POST /b"]
    assert ec.unasserted == ["GET /c"]  # observed but no matcher covers it
    assert ec.declared_unobserved == ["/never"]  # matcher that hit no exchange
    assert ec.coverage == 2 / 3


def test_body_only_event_contributes_no_endpoint_matcher() -> None:
    # A body-only event pins no endpoint — it must not crash building a (field-less) RequestMatch.
    [scn] = load_scenarios(
        "- name: x\n  steps:\n    - assert: [ { event: { body: { name: tap } } } ]\n"
    )
    assert referenced_requests(scn) == []
    ec = endpoint_coverage([scn], [_ex("GET", "/a")])
    assert ec.unasserted == ["GET /a"]  # the event declares no endpoint


def test_cli_skips_malformed_network_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - assert: [ { request: { path: /a } } ]\n", encoding="utf-8"
    )
    net = tmp_path / "runs" / "20260101-000000" / "00-x"
    net.mkdir(parents=True)
    # a bad-typed entry (status as a string) must be skipped, not crash the command
    (net / "network.json").write_text('[{"method":"GET","path":"/a","status":"oops"}]', "utf-8")
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["coverage", "--target", "demo", "--config", str(config), "--runs", str(tmp_path / "runs")],
    )
    assert result.exit_code == 0  # skipped the bad file, did not crash


def test_cli_skips_scalar_network_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: a }\n", "utf-8")
    net = tmp_path / "runs" / "20260101-000000" / "00-x"
    net.mkdir(parents=True)
    (net / "network.json").write_text("null", encoding="utf-8")  # a scalar, not a list
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["coverage", "--target", "demo", "--config", str(config), "--runs", str(tmp_path / "runs")],
    )
    assert result.exit_code == 0  # a scalar file is skipped, not iterated/crashed


def test_cli_skips_invalid_json_elements(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: home.a }\n", "utf-8")
    step = tmp_path / "runs" / "20260101-000000" / "00-x"
    step.mkdir(parents=True)
    # invalid JSON (json.JSONDecodeError is a ValueError) must be skipped, not crash the command
    (step / "elements.json").write_text("{not json", encoding="utf-8")
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["coverage", "--target", "demo", "--config", str(config), "--runs", str(tmp_path / "runs")],
    )
    assert result.exit_code == 0  # skipped the bad file, did not crash


def test_cli_runs_path_missing_warns_and_proceeds(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", "utf-8"
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "coverage",
            "--target",
            "demo",
            "--config",
            str(config),
            "--runs",
            str(tmp_path / "nope"),
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout).get("endpoints") is None  # omitted, not crashed
    assert "skipping run-evidence coverage" in result.stderr  # but the flag wasn't ignored silently


def test_cli_endpoint_coverage_with_runs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - assert: [ { request: { path: /a } } ]\n", encoding="utf-8"
    )
    # a run dir with one scenario's network.json
    net = tmp_path / "runs" / "20260101-000000" / "00-x"
    net.mkdir(parents=True)
    (net / "network.json").write_text(
        '[{"method":"GET","path":"/a"},{"method":"POST","path":"/b"}]', encoding="utf-8"
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "coverage",
            "--target",
            "demo",
            "--config",
            str(config),
            "--runs",
            str(tmp_path / "runs"),
            "--json",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["endpoints"]["asserted"] == ["GET /a"]
    assert data["endpoints"]["unasserted"] == ["POST /b"]


def test_cli_without_runs_omits_endpoint_section(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--target", "demo", "--config", str(config), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout).get("endpoints") is None


# --- BE-0050: observed-id coverage (ids rendered across a run set vs declared namespaces) ---


def test_observed_id_coverage_groups_by_namespace() -> None:
    cov = observed_id_coverage(["home.start", "home.title", "auth.email"], ["home", "auth", "cart"])
    assert cov.total == 3 and cov.covered == 2
    assert cov.coverage == 2 / 3
    assert cov.unobserved == ["cart"]  # declared, but rendered in no run
    assert [ns.namespace for ns in cov.namespaces] == ["home", "auth"]  # declared order
    assert cov.namespaces[0].ids == ["home.start", "home.title"]  # sorted, deduped
    assert cov.off_namespace == []


def test_observed_id_outside_declared_namespaces_is_off_namespace() -> None:
    cov = observed_id_coverage(["legacy.button", "home.start"], ["home"])
    assert cov.covered == 1
    assert cov.off_namespace == ["legacy.button"]  # rendered, but its namespace was never declared
    assert cov.unobserved == []


def test_observed_id_coverage_dedupes_across_runs() -> None:
    # The same id rendered in several runs counts once.
    cov = observed_id_coverage(["home.start", "home.start", "home.menu"], ["home"])
    [ns] = cov.namespaces
    assert ns.ids == ["home.menu", "home.start"]


def test_observed_id_coverage_no_declared_namespaces_is_full() -> None:
    cov = observed_id_coverage(["home.start"], [])
    assert cov.total == 0 and cov.covered == 0
    assert cov.coverage == 1.0
    assert cov.unobserved == []


def test_observed_id_coverage_nothing_observed_is_all_gaps() -> None:
    cov = observed_id_coverage([], ["home", "auth"])
    assert cov.covered == 0
    assert cov.coverage == 0.0
    assert cov.unobserved == ["home", "auth"]


def test_render_observed_ids_reports_coverage_gaps_and_off_namespace() -> None:
    cov = observed_id_coverage(["home.start", "legacy.x"], ["home", "auth"])
    text = render_observed_ids(cov)
    assert "observed ids: 0.50 (1/2)" in text
    assert "home" in text and "home.start" in text
    assert "auth" in text  # the unobserved namespace
    assert "off-namespace" in text and "legacy.x" in text


def _write_elements(step_dir, identifiers) -> None:  # type: ignore[no-untyped-def]
    step_dir.mkdir(parents=True, exist_ok=True)
    els = [
        {"identifier": i, "label": None, "traits": [], "value": None, "frame": [0, 0, 1, 1]}
        for i in identifiers
    ]
    (step_dir / "elements.json").write_text(json.dumps(els), encoding="utf-8")


def test_cli_observed_id_coverage_with_runs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    # two step dirs under a run, each with an elements.json
    _write_elements(tmp_path / "runs" / "20260101-000000" / "00-x", ["home.start", None])
    _write_elements(tmp_path / "runs" / "20260101-000000" / "01-x", ["auth.email"])
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home, auth, cart]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "coverage",
            "--target",
            "demo",
            "--config",
            str(config),
            "--runs",
            str(tmp_path / "runs"),
            "--json",
        ],
    )
    assert result.exit_code == 0
    obs = json.loads(result.stdout)["observed_ids"]
    assert obs["covered"] == 2 and obs["total"] == 3
    assert obs["unobserved"] == ["cart"]  # declared but rendered in no run
    assert obs["namespaces"][0]["namespace"] == "home"
    assert obs["namespaces"][0]["ids"] == ["home.start"]  # null identifiers ignored


def test_cli_observed_id_coverage_ignores_empty_identifiers(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    # an element with an empty-string identifier carries no stable id — it must not contribute
    _write_elements(tmp_path / "runs" / "20260101-000000" / "00-x", ["home.start", ""])
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "coverage",
            "--target",
            "demo",
            "--config",
            str(config),
            "--runs",
            str(tmp_path / "runs"),
            "--json",
        ],
    )
    assert result.exit_code == 0
    obs = json.loads(result.stdout)["observed_ids"]
    assert obs["namespaces"][0]["ids"] == ["home.start"]  # the "" id is dropped, not bucketed
    assert obs["off_namespace"] == []  # an empty id must not become an off-namespace ("") entry


def test_cli_observed_id_coverage_text_output(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    _write_elements(tmp_path / "runs" / "20260101-000000" / "00-x", ["home.start"])
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home, auth]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["coverage", "--target", "demo", "--config", str(config), "--runs", str(tmp_path / "runs")],
    )
    assert result.exit_code == 0
    assert "observed ids:" in result.stdout
    assert "auth" in result.stdout  # the unobserved namespace


def test_cli_skips_malformed_elements_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: a }\n", "utf-8")
    step = tmp_path / "runs" / "20260101-000000" / "00-x"
    step.mkdir(parents=True)
    (step / "elements.json").write_text("null", encoding="utf-8")  # a scalar, not a list
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["coverage", "--target", "demo", "--config", str(config), "--runs", str(tmp_path / "runs")],
    )
    assert result.exit_code == 0  # a scalar file is skipped, not crashed
    assert (
        json.loads(  # observed-id section still emitted, just empty
            runner.invoke(
                app,
                [
                    "coverage",
                    "--target",
                    "demo",
                    "--config",
                    str(config),
                    "--runs",
                    str(tmp_path / "runs"),
                    "--json",
                ],
            ).stdout
        )["observed_ids"]["covered"]
        == 0
    )


def test_cli_without_runs_omits_observed_id_section(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--target", "demo", "--config", str(config), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout).get("observed_ids") is None


# --- BE-0050: HTML coverage report (visualizes the three dimensions on one self-contained page) ---


def test_render_html_is_self_contained_and_shows_the_static_dimension() -> None:
    cov = _cov(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n    - tap: { id: legacy.x }\n",
        ["home", "auth"],
    )
    html = render_html(cov, target="demo")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert (
        "<style>" in html and 'src="' not in html
    )  # self-contained: inline CSS, no external asset
    assert "demo" in html  # the target name
    assert "1/2" in html  # covered / total
    assert "home.start" in html  # a referenced id under a covered namespace
    assert "auth" in html  # the gap namespace
    assert "legacy.x" in html  # an off-namespace id


def test_render_html_without_run_evidence_omits_those_sections() -> None:
    cov = _cov("- name: x\n  steps:\n    - tap: { id: home.start }\n", ["home"])
    html = render_html(cov)
    assert "endpoint" not in html.lower()
    assert "observed" not in html.lower()


def test_render_html_includes_endpoint_and_observed_sections_when_given() -> None:
    cov = _cov("- name: x\n  steps:\n    - assert: [ { request: { path: /a } } ]\n", ["home"])
    ec = endpoint_coverage(load_scenarios("- name: x\n  steps: []\n"), [_ex("POST", "/b")])
    oc = observed_id_coverage(["home.start"], ["home", "auth"])
    html = render_html(cov, endpoints=ec, observed=oc)
    assert "POST /b" in html  # unasserted observed traffic
    assert "auth" in html  # namespace observed in no run


def test_render_html_escapes_ids() -> None:
    # ids never normally carry markup, but the report must not emit raw "<" into the page.
    cov = _cov("- name: x\n  steps:\n    - tap: { id: '<svg>.x' }\n", ["<svg>"])
    html = render_html(cov)
    assert "<svg>.x" not in html  # escaped, not injected verbatim
    assert "&lt;svg&gt;.x" in html


def test_cli_writes_html_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n", encoding="utf-8"
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home, auth]\n",
        encoding="utf-8",
    )
    out = tmp_path / "coverage.html"
    result = runner.invoke(
        app,
        ["coverage", "--target", "demo", "--config", str(config), "--html", str(out)],
    )
    assert result.exit_code == 0  # read-only: never gates
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert body.lstrip().startswith("<!DOCTYPE html>")
    assert "home.start" in body and "auth" in body  # a covered id and the gap


def _html_only_config(tmp_path):  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: home.a }\n", "utf-8")
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    return config


def test_cli_html_creates_missing_parent_dirs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _html_only_config(tmp_path)
    out = tmp_path / "nested" / "dir" / "coverage.html"  # parents do not exist yet
    result = runner.invoke(
        app, ["coverage", "--target", "demo", "--config", str(config), "--html", str(out)]
    )
    assert result.exit_code == 0
    assert out.is_file()  # the parent dirs were created for the user-requested output


def test_cli_html_unwritable_path_exits_2_cleanly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _html_only_config(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")  # a file where a parent dir would need to be
    out = blocker / "coverage.html"  # writing here can't succeed (parent is a file)
    result = runner.invoke(
        app, ["coverage", "--target", "demo", "--config", str(config), "--html", str(out)]
    )
    assert result.exit_code == 2  # clean failure, not an uncaught traceback
    assert "Traceback" not in result.stdout


# --- BE-0050: screens-visited coverage (crawl-discovered screens vs the screens runs reached) ---


def _ref(fp: str, label: str) -> ScreenRef:
    return ScreenRef(fingerprint=fp, label=label)


def test_screen_coverage_splits_visited_and_unvisited() -> None:
    discovered = [_ref("aaa", "home"), _ref("bbb", "detail"), _ref("ccc", "settings")]
    sc = screen_coverage(discovered, frozenset({"aaa", "ccc"}))
    assert sc.total == 3 and sc.covered == 2
    assert sc.coverage == 2 / 3
    assert [s.fingerprint for s in sc.visited] == ["aaa", "ccc"]
    assert [s.label for s in sc.unvisited] == ["detail"]  # discovered, no run reached it


def test_screen_coverage_dedupes_discovered_by_fingerprint() -> None:
    # the same fingerprint listed twice counts once, keeping the first-listed label
    sc = screen_coverage([_ref("aaa", "home"), _ref("aaa", "home-alt")], frozenset())
    assert sc.total == 1
    assert sc.unvisited[0].label == "home"  # first-listed label wins


def test_screen_coverage_nothing_discovered_is_full() -> None:
    sc = screen_coverage([], frozenset({"aaa"}))
    assert sc.total == 0 and sc.coverage == 1.0
    assert sc.unvisited == []


def test_screen_coverage_visited_ignores_screens_off_the_map() -> None:
    # a run fingerprint the crawl never discovered does not inflate coverage
    sc = screen_coverage([_ref("aaa", "home")], frozenset({"aaa", "zzz"}))
    assert sc.covered == 1 and sc.total == 1


def test_render_screens_reports_coverage_and_unvisited() -> None:
    sc = screen_coverage([_ref("aaa", "home"), _ref("bbb", "detail")], frozenset({"aaa"}))
    text = render_screens(sc)
    assert "screens visited: 0.50 (1/2)" in text
    assert "detail" in text  # the unvisited screen's label


def test_render_html_includes_screens_section_when_given() -> None:
    cov = _cov("- name: x\n  steps:\n    - tap: { id: home.start }\n", ["home"])
    sc = screen_coverage([_ref("aaa", "home"), _ref("bbb", "detail")], frozenset({"aaa"}))
    html = render_html(cov, screens=sc)
    assert "Screens visited" in html
    assert "1/2" in html
    assert "detail" in html  # the unvisited screen
    # absent without a screens dimension
    assert "Screens visited" not in render_html(cov)


def _screenmap(path, nodes) -> None:  # type: ignore[no-untyped-def]
    path.write_text(
        json.dumps({"nodes": nodes, "edges": [], "crashes": [], "alerts": []}), encoding="utf-8"
    )


def test_cli_screen_coverage_with_crawl_and_runs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: home.a }\n", "utf-8")
    # a run that rendered a screen with two ids — fingerprint it the same way the crawl does
    els = [
        {"identifier": "home.a", "label": None, "traits": [], "value": None, "frame": [0, 0, 1, 1]},
        {"identifier": "home.b", "label": None, "traits": [], "value": None, "frame": [0, 0, 1, 1]},
    ]
    visited_fp = screen_fingerprint(els).value
    step = tmp_path / "runs" / "20260101-000000" / "00-x"
    step.mkdir(parents=True)
    (step / "elements.json").write_text(json.dumps(els), encoding="utf-8")
    # a crawl that discovered the visited screen plus one the run never reached
    screenmap = tmp_path / "screenmap.json"
    _screenmap(
        screenmap,
        [
            {"fingerprint": visited_fp, "kind": "id", "ids": ["home.a", "home.b"]},
            {"fingerprint": "neverreached", "kind": "id", "ids": ["secret.panel"]},
        ],
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "coverage",
            "--target",
            "demo",
            "--config",
            str(config),
            "--runs",
            str(tmp_path / "runs"),
            "--crawl",
            str(screenmap),
            "--json",
        ],
    )
    assert result.exit_code == 0
    screens = json.loads(result.stdout)["screens"]
    assert screens["covered"] == 1 and screens["total"] == 2
    assert screens["unvisited"][0]["label"] == "secret.panel"  # the discovered, unreached screen


def test_cli_accepts_a_crawl_run_dir_for_screenmap(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: home.a }\n", "utf-8")
    crawl_dir = tmp_path / "runs" / "20260101-000000"
    (crawl_dir / "00-x").mkdir(parents=True)
    _screenmap(
        crawl_dir / "screenmap.json", [{"fingerprint": "aaa", "kind": "id", "ids": ["home"]}]
    )
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "coverage",
            "--target",
            "demo",
            "--config",
            str(config),
            "--runs",
            str(tmp_path / "runs"),
            "--crawl",
            str(crawl_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["screens"]["total"] == 1  # screenmap.json found inside the dir


def test_cli_crawl_without_runs_warns_and_skips(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: home.a }\n", "utf-8")
    _screenmap(tmp_path / "screenmap.json", [{"fingerprint": "aaa", "kind": "id", "ids": ["home"]}])
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "coverage",
            "--target",
            "demo",
            "--config",
            str(config),
            "--crawl",
            str(tmp_path / "screenmap.json"),
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert (
        json.loads(result.stdout).get("screens") is None
    )  # needs run evidence to know what was visited
    assert "needs --runs" in result.stderr


def test_cli_without_crawl_omits_screen_section(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: x\n  steps:\n    - tap: { id: home.a }\n", "utf-8")
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["coverage", "--target", "demo", "--config", str(config), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout).get("screens") is None

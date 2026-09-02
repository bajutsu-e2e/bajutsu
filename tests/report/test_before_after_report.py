"""Report coverage for the `before` / `after` lifecycle phases (BE-0392).

Their outcomes sit beside `steps` rather than inside it, so every export that reads `RunResult.steps`
directly would drop a run whose only failure was in setup or teardown. Each of the four — the HTML
Result panel, the JUnit `<failure>` body, the CTRF record, and the manifest round trip — is covered
here.
"""

from __future__ import annotations

import json

from _report import _el

from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.scenario import Scenario
from bajutsu.config import load_config, resolve
from bajutsu.orchestrator import RunResult, run_scenario
from bajutsu.report import html_report, junit_xml, manifest_dict
from bajutsu.report.ctrf import ctrf_json
from bajutsu.report.html import scenario_render_inputs
from bajutsu.report.load import results_from_manifest
from bajutsu.runner.pipeline import with_lifecycle_phases

_SCREEN = [_el("a", "A", ["button"]), _el("c", "C", ["button"])]


def _run(data: dict[str, object]) -> tuple[RunResult, Scenario]:
    scenario = Scenario.model_validate({"name": "lifecycle", **data})
    return run_scenario(FakeDriver(list(_SCREEN)), scenario), scenario


def _failing_success_rule() -> tuple[RunResult, Scenario]:
    # An otherwise-passing run whose `success` cleanup is the only thing that failed — the case that
    # exists nowhere in `steps`, so an export reading `steps` alone reports a green run.
    return _run(
        {
            "steps": [{"tap": {"id": "a"}}],
            "after": [{"on": "success", "steps": [{"tap": {"id": "missing"}}]}],
        }
    )


def test_junit_failure_body_names_the_after_rule_that_failed() -> None:
    result, _ = _failing_success_rule()
    xml = junit_xml([result])
    assert "<failure" in xml
    assert "after step 0 tap: FAIL" in xml


def test_junit_failure_body_names_a_failing_before_step() -> None:
    result, _ = _run({"before": [{"tap": {"id": "missing"}}], "steps": [{"tap": {"id": "a"}}]})
    assert "before step 0 tap: FAIL" in junit_xml([result])


def test_ctrf_record_carries_both_phases() -> None:
    result, _ = _failing_success_rule()
    test = ctrf_json("r", [result])["results"]["tests"][0]  # type: ignore[index]
    assert test["status"] == "failed"
    assert test["extra"]["after"][0]["status"] == "failed"
    assert "before" not in test["extra"]  # pruned when the scenario declares none


def test_manifest_round_trips_both_phases_and_the_verdict() -> None:
    result, _ = _run(
        {
            "before": [{"tap": {"id": "a"}}],
            "steps": [{"tap": {"id": "a"}}],
            "after": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
        }
    )
    reloaded = results_from_manifest(json.loads(json.dumps(manifest_dict("r", [result]))))[0]
    assert [o.action for o in reloaded.before_outcomes] == ["tap"]
    assert [o.action for o in reloaded.after_outcomes] == ["tap"]
    assert reloaded.after_verdict == "success"


def test_html_shows_the_phases_as_their_own_blocks() -> None:
    result, scenario = _run(
        {
            "before": [{"tap": {"id": "a"}}],
            "steps": [{"tap": {"id": "a"}}],
            "after": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
        }
    )
    definitions, sources = scenario_render_inputs([scenario])
    html = html_report("r", [result], definitions=definitions, sources=sources)
    assert '<span class="deflbl">before</span>' in html
    assert '<span class="deflbl">after</span>' in html
    # The rule's outcome word rides the `#` cell, so a reader can tell unconditional teardown from
    # teardown this run's verdict selected without a second table.
    assert "always·0" in html


def test_html_after_block_shows_only_the_dispatched_rules() -> None:
    result, scenario = _run(
        {
            "steps": [{"tap": {"id": "missing"}}],
            "after": [
                {"on": "success", "steps": [{"tap": {"id": "a"}}]},
                {"on": "error", "steps": [{"tap": {"id": "c"}}]},
            ],
        }
    )
    definitions, sources = scenario_render_inputs([scenario])
    html = html_report("r", [result], definitions=definitions, sources=sources)
    assert result.after_verdict == "error"
    assert "error·0" in html
    assert "success·0" not in html


def test_html_after_block_discloses_the_steps_a_failing_rule_never_reached() -> None:
    result, scenario = _run(
        {
            "steps": [{"tap": {"id": "a"}}],
            "after": [
                {
                    "on": "always",
                    "steps": [{"tap": {"id": "missing"}}, {"tap": {"id": "c"}}],
                }
            ],
        }
    )
    definitions, sources = scenario_render_inputs([scenario])
    html = html_report("r", [result], definitions=definitions, sources=sources)
    assert len(result.after_outcomes) == 1  # the phase stopped the rule at its first failure
    assert "always·—" in html  # …and the step it never reached is disclosed, without a number


_APP_WIDE_CONFIG = """
targets:
  app:
    bundleId: com.example.app
    before:
      - tap: { id: a }
    after:
      - on: always
        steps: [{ tap: { id: c } }]
"""


def test_an_app_wide_phase_reports_against_its_own_step_definition() -> None:
    # The report's plan and the run's steps must come from one object. Were the config's phases
    # passed to the runner beside the scenario instead of folded into it, the app-wide before-step's
    # outcome would render with the scenario's own step definition in the detail column, and the
    # app-wide after rule's outcome would be dropped from the After block entirely.
    eff = resolve(load_config(_APP_WIDE_CONFIG), "app")
    scenario = with_lifecycle_phases(
        eff,
        [
            Scenario.model_validate(
                {
                    "name": "lifecycle",
                    "before": [{"tap": {"id": "c"}}],
                    "steps": [{"tap": {"id": "a"}}],
                    "after": [{"on": "always", "steps": [{"tap": {"id": "a"}}]}],
                }
            )
        ],
    )[0]
    result = run_scenario(FakeDriver(list(_SCREEN)), scenario)
    definitions, sources = scenario_render_inputs([scenario])

    assert [o.action for o in result.before_outcomes] == ["tap", "tap"]
    assert len(result.after_outcomes) == 2
    plan = definitions[0]
    assert [next(iter(s.values()))["id"] for s in plan["before"]] == ["a", "c"]
    assert [r["steps"][0]["tap"]["id"] for r in plan["after"]] == ["a", "c"]
    # Both phases render, and the rendered plan is the one the run executed.
    html = html_report("r", [result], definitions=definitions, sources=sources)
    assert '<span class="deflbl">before</span>' in html
    assert "always\u00b70" in html and "always\u00b71" in html

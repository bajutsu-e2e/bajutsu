"""Report coverage for the `setup` / `cleanup` lifecycle phases (BE-0392, BE-0452).

Their outcomes sit beside `steps` rather than inside it, so every export that reads `RunResult.steps`
directly would drop a run whose only failure was in setup or teardown. Each of the four — the HTML
Result panel, the JUnit `<failure>` body, the CTRF record, and the manifest round trip — is covered
here.
"""

from __future__ import annotations

import json
from pathlib import Path

from _report import _el

from bajutsu.common.config import load_config, resolve
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import RunResult, run_scenario
from bajutsu.common.report import html_report, junit_xml, manifest_dict
from bajutsu.common.report.ctrf import ctrf_json
from bajutsu.common.report.html import scenario_render_inputs
from bajutsu.common.report.load import load_run, results_from_manifest
from bajutsu.common.runner.pipeline import with_lifecycle_phases
from bajutsu.common.scenario import Scenario

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
            "cleanup": [{"on": "success", "steps": [{"tap": {"id": "missing"}}]}],
        }
    )


def test_junit_failure_body_names_the_cleanup_rule_that_failed() -> None:
    result, _ = _failing_success_rule()
    xml = junit_xml([result])
    assert "<failure" in xml
    assert "cleanup step 0 tap: FAIL" in xml


def test_junit_failure_body_names_a_failing_setup_step() -> None:
    result, _ = _run({"setup": [{"tap": {"id": "missing"}}], "steps": [{"tap": {"id": "a"}}]})
    assert "setup step 0 tap: FAIL" in junit_xml([result])


def test_ctrf_record_carries_both_phases() -> None:
    result, _ = _failing_success_rule()
    test = ctrf_json("r", [result])["results"]["tests"][0]  # type: ignore[index]
    assert test["status"] == "failed"
    assert test["extra"]["cleanup"][0]["status"] == "failed"
    assert "setup" not in test["extra"]  # pruned when the scenario declares none


def test_manifest_round_trips_both_phases_and_the_verdict() -> None:
    result, _ = _run(
        {
            "setup": [{"tap": {"id": "a"}}],
            "steps": [{"tap": {"id": "a"}}],
            "cleanup": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
        }
    )
    reloaded = results_from_manifest(json.loads(json.dumps(manifest_dict("r", [result]))))[0]
    assert [o.action for o in reloaded.before_outcomes] == ["tap"]
    assert [o.action for o in reloaded.after_outcomes] == ["tap"]
    assert reloaded.after_verdict == "success"


def test_html_shows_the_phases_as_their_own_blocks() -> None:
    result, scenario = _run(
        {
            "setup": [{"tap": {"id": "a"}}],
            "steps": [{"tap": {"id": "a"}}],
            "cleanup": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
        }
    )
    definitions, sources = scenario_render_inputs([scenario])
    html = html_report("r", [result], definitions=definitions, sources=sources)
    assert '<span class="deflbl">setup</span>' in html
    assert '<span class="deflbl">cleanup</span>' in html
    # The rule's outcome word rides the `#` cell, so a reader can tell unconditional teardown from
    # teardown this run's verdict selected without a second table.
    assert "always·0" in html
    # Each phase's rows carry the `data-phase` the seekbar's `bands()` keys off (report.js) to
    # draw the setup/cleanup bands — distinct from the scenario's own steps, which carry none.
    assert "data-phase='setup'" in html
    assert "data-phase='cleanup'" in html


def test_html_cleanup_block_shows_only_the_dispatched_rules() -> None:
    result, scenario = _run(
        {
            "steps": [{"tap": {"id": "missing"}}],
            "cleanup": [
                {"on": "success", "steps": [{"tap": {"id": "a"}}]},
                {"on": "failure", "steps": [{"tap": {"id": "c"}}]},
            ],
        }
    )
    definitions, sources = scenario_render_inputs([scenario])
    html = html_report("r", [result], definitions=definitions, sources=sources)
    assert result.after_verdict == "failure"
    assert "failure·0" in html
    assert "success·0" not in html


def test_html_cleanup_block_discloses_the_steps_a_failing_rule_never_reached() -> None:
    result, scenario = _run(
        {
            "steps": [{"tap": {"id": "a"}}],
            "cleanup": [
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


_APP_WIDE_CONFIG = (
    "targets:\n"
    "  app:\n"
    "    platform: ios\n"
    "    app: {id: com.example.app}\n"
    "    hooks:\n"
    "      setup:\n"
    "      - tap: {id: a}\n"
    "      cleanup:\n"
    "      - 'on': always\n"
    "        steps:\n"
    "        - tap: {id: c}\n"
)


def test_an_app_wide_phase_reports_against_its_own_step_definition() -> None:
    # The report's plan and the run's steps must come from one object. Were the config's phases
    # passed to the runner beside the scenario instead of folded into it, the app-wide setup step's
    # outcome would render with the scenario's own step definition in the detail column, and the
    # app-wide cleanup rule's outcome would be dropped from the cleanup block entirely.
    eff = resolve(load_config(_APP_WIDE_CONFIG), "app")
    scenario = with_lifecycle_phases(
        eff,
        [
            Scenario.model_validate(
                {
                    "name": "lifecycle",
                    "setup": [{"tap": {"id": "c"}}],
                    "steps": [{"tap": {"id": "a"}}],
                    "cleanup": [{"on": "always", "steps": [{"tap": {"id": "a"}}]}],
                }
            )
        ],
    )[0]
    result = run_scenario(FakeDriver(list(_SCREEN)), scenario)
    definitions, sources = scenario_render_inputs([scenario])

    assert [o.action for o in result.before_outcomes] == ["tap", "tap"]
    assert len(result.after_outcomes) == 2
    plan = definitions[0]
    assert [next(iter(s.values()))["id"] for s in plan["setup"]] == ["a", "c"]
    assert [r["steps"][0]["tap"]["id"] for r in plan["cleanup"]] == ["a", "c"]
    # Both phases render, and the rendered plan is the one the run executed.
    html = html_report("r", [result], definitions=definitions, sources=sources)
    assert '<span class="deflbl">setup</span>' in html
    assert "always\u00b70" in html and "always\u00b71" in html


# A run stored before BE-0452: `before` / `after` in its plan, and `error` as both the dispatched
# verdict and the outcome word of a cleanup rule and a capturePolicy trigger.
_PRE_RENAME_RUN = Path(__file__).parent.parent / "fixtures" / "be0452" / "pre_rename_run"


def test_a_run_stored_before_the_rename_still_loads() -> None:
    model = load_run(_PRE_RENAME_RUN)
    (result,) = model.results
    assert result.after_verdict == "failure"
    (plan,) = model.definitions
    assert [r["on"] for r in plan["cleanup"]] == ["success", "failure"]
    assert plan["capturePolicy"][0]["on"] == {"result": "failure"}
    assert "before" not in plan and "after" not in plan
    html = html_report(
        model.run_id, model.results, definitions=model.definitions, sources=model.sources
    )
    assert '<span class="deflbl">setup</span>' in html
    # The rule the old `error` verdict dispatched is still the one lined up with its outcome.
    assert "failure·0" in html
    assert "success·" not in html


def test_the_plan_upgrade_leaves_what_it_does_not_recognize() -> None:
    from bajutsu.common import _yaml
    from bajutsu.common.scenario.load import _upgraded_scenario_text

    # Not a list of scenarios: handed back untouched for the scenario loader to report.
    assert _upgraded_scenario_text("name: x\n") == "name: x\n"
    text = (
        "scenarios:\n"
        "  - not a scenario\n"
        "  - name: s\n"
        "    after: [junk, {'on': always, steps: []}]\n"
        "    capturePolicy: [junk, {'on': {event: screenChanged}, capture: [elements]}]\n"
    )
    (_, plan) = _yaml.safe_load(_upgraded_scenario_text(text))["scenarios"]
    assert plan["cleanup"] == ["junk", {"on": "always", "steps": []}]
    assert plan["capturePolicy"][1]["on"] == {"event": "screenChanged"}


_PRE_RENAME_PLAN = (
    "- name: lifecycle\n"
    "  before: [{tap: {id: a}}]\n"
    "  steps: [{tap: {id: b}, from: tap b}]\n"
    "  after: [{'on': error, steps: [{tap: {id: c}}]}]\n"
)


def test_trace_still_reads_provenance_from_a_pre_rename_run(tmp_path: Path) -> None:
    from bajutsu.analysis.trace import _provenance_by_scenario

    (tmp_path / "scenario.yaml").write_text(_PRE_RENAME_PLAN, encoding="utf-8")
    assert _provenance_by_scenario(tmp_path) == {"lifecycle": ["tap b"]}


def test_triage_still_finds_the_scenario_of_a_pre_rename_run(tmp_path: Path) -> None:
    from bajutsu.triage.heuristic._functions import _load_scenario

    (tmp_path / "scenario.yaml").write_text(_PRE_RENAME_PLAN, encoding="utf-8")
    scenario = _load_scenario(tmp_path, "lifecycle")
    assert scenario is not None
    assert [r.on for r in scenario.cleanup] == ["failure"]

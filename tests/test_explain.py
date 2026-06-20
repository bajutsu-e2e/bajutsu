"""Tests for the capturePolicy `--explain` dry run (bajutsu/trace.py, BE-0028).

The dry run statically previews how a scenario's capturePolicy would fire before a run pays for
it. Action-triggered rules are counted exactly (reusing the run loop's own matcher);
`event`/`result` rules are runtime-dependent, so they are reported as conditional, not counted.
Heavy captures (video / deviceLog / appTrace / network) on broadly-matching rules are flagged.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bajutsu import trace
from bajutsu.cli import app
from bajutsu.scenario import load_scenarios

runner = CliRunner()

# Two rules: a heavy capture on a leading-glob action rule (broad), and a screenChanged rule.
SCENARIO = """
- name: demo
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [video, screenshot]
    - on: { event: screenChanged }
      capture: [network]
    - on: { result: error }
      capture: [video, deviceLog]
  steps:
    - tap: { id: form.submit }
    - tap: { id: home.open }
    - tap: { id: dialog.submit }
"""


def _rules() -> list[trace.RuleExplain]:
    return trace.explain_capture(load_scenarios(SCENARIO)[0])


def test_action_rule_counts_matching_steps() -> None:
    submit = _rules()[0]
    assert submit.countable is True
    assert submit.count == 2  # form.submit + dialog.submit, not home.open
    assert any("form.submit" in s for s in submit.steps)
    assert any("dialog.submit" in s for s in submit.steps)
    assert all("home.open" not in s for s in submit.steps)


def test_action_rule_flags_heavy_capture_on_broad_glob() -> None:
    submit = _rules()[0]
    assert "video" in submit.heavy
    assert "screenshot" not in submit.heavy  # instant, not heavy
    assert submit.broad is True  # leading-"*" glob matches broadly
    assert submit.warn is True


def test_event_rule_is_conditional_not_counted() -> None:
    screen_changed = _rules()[1]
    assert screen_changed.countable is False
    assert screen_changed.count == 0
    assert "network" in screen_changed.heavy
    assert screen_changed.broad is True  # fires on every screen change
    assert screen_changed.warn is True


def test_error_rule_is_conditional_but_not_broad() -> None:
    # The result:error safety net is heavy but only fires on failure — not flagged as broad.
    on_error = _rules()[2]
    assert on_error.countable is False
    assert set(on_error.heavy) == {"video", "deviceLog"}
    assert on_error.broad is False
    assert on_error.warn is False


def test_narrow_action_rule_is_not_broad() -> None:
    scenario = load_scenarios(
        """
- name: narrow
  capturePolicy:
    - on: { action: tap, idMatches: "form.submit" }
      capture: [video]
  steps:
    - tap: { id: form.submit }
    - tap: { id: home.open }
"""
    )[0]
    rule = trace.explain_capture(scenario)[0]
    assert rule.count == 1
    assert rule.broad is False  # an exact id, no leading glob
    assert rule.warn is False  # heavy but narrow -> no warning


def test_render_explain_reports_counts_and_warns() -> None:
    out = trace.render_explain(load_scenarios(SCENARIO))
    assert "demo" in out
    assert "2" in out  # the submit rule's firing count
    assert "⚠" in out  # at least one heavy+broad warning
    assert "screenChanged" in out


def test_cli_trace_explain_reports_firing(tmp_path: Path) -> None:
    scn = tmp_path / "demo.yaml"
    scn.write_text(SCENARIO, encoding="utf-8")
    r = runner.invoke(app, ["trace", "--explain", str(scn)])
    assert r.exit_code == 0
    assert "fires 2×" in r.output
    assert "⚠" in r.output


def test_cli_trace_explain_missing_file() -> None:
    r = runner.invoke(app, ["trace", "--explain", "/no/such/scenario.yaml"])
    assert r.exit_code == 2
    assert "needs a scenario file" in r.output

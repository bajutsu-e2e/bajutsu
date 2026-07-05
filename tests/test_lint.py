"""Tests for the scenario linter (bajutsu lint)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.lint import lint_diagnostics, lint_file, lint_text, provenance_coverage
from bajutsu.scenario import load_scenario_file

runner = CliRunner()


def test_valid_scenario_passes() -> None:
    text = "- name: a\n  steps:\n    - tap: { id: ok }\n"
    errors = lint_text(text)
    assert errors == []


def test_provenance_coverage_counts_steps_with_from() -> None:
    # Advisory only (BE-0044): how many top-level steps carry `from:`.
    text = (
        "- name: a\n  steps:\n    - tap: { id: one }\n      from: tap one\n    - tap: { id: two }\n"
    )
    scenarios = load_scenario_file(text).scenarios
    assert provenance_coverage(scenarios) == "provenance: 1/2 step(s) carry `from:`"


def test_provenance_coverage_is_none_without_steps() -> None:
    # No steps to report on (e.g. an empty file) → no advisory, never an error.
    assert provenance_coverage([]) is None


def test_missing_name_fails() -> None:
    text = "- steps:\n    - tap: { id: ok }\n"
    errors = lint_text(text)
    assert len(errors) == 1
    assert "name" in errors[0].lower()


def test_unknown_action_fails() -> None:
    text = "- name: a\n  steps:\n    - fly: { id: ok }\n"
    errors = lint_text(text)
    assert len(errors) >= 1


def test_extra_field_fails() -> None:
    text = "- name: a\n  steps:\n    - tap: { id: ok }\n  bogus: true\n"
    errors = lint_text(text)
    assert len(errors) >= 1


def test_invalid_yaml_fails() -> None:
    text = "- name: a\n  steps:\n    - tap: {id: ok\n"  # missing closing brace
    errors = lint_text(text)
    assert len(errors) >= 1


def test_lint_file_reads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "good.yaml"
    path.write_text("- name: ok\n  steps:\n    - tap: { id: x }\n", encoding="utf-8")
    assert lint_file(path) == []


def test_lint_file_reports_errors(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- steps:\n    - tap: { id: x }\n", encoding="utf-8")
    errors = lint_file(path)
    assert len(errors) >= 1


def test_lint_file_missing_file(tmp_path: Path) -> None:
    errors = lint_file(tmp_path / "nonexistent.yaml")
    assert len(errors) == 1
    assert "not found" in errors[0].lower()


def test_multiple_scenarios_all_validated() -> None:
    text = (
        "- name: a\n  steps:\n    - tap: { id: ok }\n"
        "- name: b\n  steps:\n    - tap: { id: ok }\n  bogus: 1\n"
    )
    errors = lint_text(text)
    assert len(errors) >= 1


# Line-anchored diagnostics (BE-0138): the serve editor surfaces these inline, so each finding
# carries a source line where one can be resolved (parse errors exactly, validation errors
# best-effort by walking the YAML node tree), degrading to `line: None` when it cannot.


def test_diagnostics_valid_scenario_is_empty() -> None:
    assert lint_diagnostics("- name: a\n  steps:\n    - tap: { id: ok }\n") == []


def test_diagnostics_yaml_syntax_error_carries_a_line() -> None:
    # A missing closing brace is a parse error — PyYAML reports an exact mark.
    diags = lint_diagnostics("- name: a\n  steps:\n    - tap: {id: ok\n")
    assert len(diags) == 1
    assert diags[0]["severity"] == "error"
    assert diags[0]["line"] is not None


def test_diagnostics_missing_name_resolves_to_the_scenario_line() -> None:
    # A bare-list file: the linter strips the synthetic `scenarios` loc prefix and anchors the
    # missing-field error to the offending scenario mapping (line 1).
    diags = lint_diagnostics("- steps:\n    - tap: { id: ok }\n")
    assert len(diags) == 1
    assert diags[0]["line"] == 1
    assert "name" in diags[0]["message"].lower()


def test_diagnostics_deep_field_resolves_to_its_line() -> None:
    # `steps` must be a list; the value sits on line 2, and the walk resolves the loc to it.
    diags = lint_diagnostics("- name: a\n  steps: notalist\n")
    assert len(diags) == 1
    assert diags[0]["line"] == 2


def test_diagnostics_top_level_type_error_has_no_line() -> None:
    # Neither a list nor a mapping: load_scenario_file raises a ValueError with no location.
    diags = lint_diagnostics("42\n")
    assert len(diags) == 1
    assert diags[0]["line"] is None
    assert diags[0]["severity"] == "error"


# CLI command coverage (BE-0117): the `bajutsu lint` entry point's branches, exercised through the
# Typer app so the command body — not just the underlying lint_* functions — is covered.


def test_cli_lint_file_not_found(tmp_path: Path) -> None:
    r = runner.invoke(app, ["lint", str(tmp_path / "missing.yaml")])
    assert r.exit_code == 1
    assert "file not found" in r.output


def test_cli_lint_unreadable_file(tmp_path: Path) -> None:
    # A directory exists() but read_text() raises IsADirectoryError (an OSError), driving the
    # read-error branch without mocking the filesystem.
    r = runner.invoke(app, ["lint", str(tmp_path)])
    assert r.exit_code == 1
    assert "read error" in r.output


def test_cli_lint_reports_errors(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- steps:\n    - tap: { id: x }\n", encoding="utf-8")  # missing name
    r = runner.invoke(app, ["lint", str(path)])
    assert r.exit_code == 1
    assert "name" in r.output.lower()


def test_cli_lint_clean_with_provenance_advisory(tmp_path: Path) -> None:
    path = tmp_path / "good.yaml"
    path.write_text("- name: a\n  steps:\n    - tap: { id: ok }\n", encoding="utf-8")
    r = runner.invoke(app, ["lint", str(path)])
    assert r.exit_code == 0
    assert "ok" in r.output
    assert "provenance:" in r.output  # 0/1 step(s) carry `from:`


def test_cli_lint_clean_without_provenance_advisory(tmp_path: Path) -> None:
    # No steps → provenance_coverage returns None → no advisory line, still a clean exit.
    path = tmp_path / "empty.yaml"
    path.write_text("[]\n", encoding="utf-8")
    r = runner.invoke(app, ["lint", str(path)])
    assert r.exit_code == 0
    assert "ok" in r.output
    assert "provenance:" not in r.output

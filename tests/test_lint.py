"""Tests for the scenario linter (bajutsu lint)."""

from __future__ import annotations

from pathlib import Path

from bajutsu.lint import lint_file, lint_text, provenance_coverage
from bajutsu.scenario import load_scenario_file


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

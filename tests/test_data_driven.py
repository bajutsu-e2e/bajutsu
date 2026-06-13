"""Tests for data-driven scenarios (data / dataFile + expand_data)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.scenario import expand_data, load_scenarios, read_csv


def _no_csv(_ref: str) -> list[dict[str, str]]:
    raise AssertionError("CSV resolver should not be called for inline data")


def test_inline_data_expands_per_row() -> None:
    scns = load_scenarios(
        """
- name: search
  data:
    - { q: dog }
    - { q: cat }
  steps:
    - type: { into: { id: search.field }, text: "${row.q}", submit: true }
  expect:
    - exists: { idMatches: "result.*" }
"""
    )
    out = expand_data(scns, _no_csv)
    assert len(out) == 2
    assert out[0].steps[0].type is not None and out[0].steps[0].type.text == "dog"
    assert out[1].steps[0].type is not None and out[1].steps[0].type.text == "cat"
    # Derived names carry the row, and data is cleared on the instances.
    assert out[0].name == "search [row 1: q=dog]"
    assert out[1].name == "search [row 2: q=cat]"
    assert all(s.data is None and s.data_file is None for s in out)


def test_non_data_scenario_passes_through() -> None:
    scns = load_scenarios("- name: s\n  steps:\n    - tap: { id: x }\n")
    out = expand_data(scns, _no_csv)
    assert len(out) == 1 and out[0] is scns[0]


def test_data_file_uses_resolver() -> None:
    scns = load_scenarios(
        '- name: s\n  dataFile: cases.csv\n  steps:\n    - tap: { id: "${row.target}" }\n'
    )
    rows = read_csv("target\nbtn.a\nbtn.b\n")
    out = expand_data(scns, lambda ref: rows if ref == "cases.csv" else [])
    assert [s.steps[0].tap.id for s in out] == ["btn.a", "btn.b"]  # type: ignore[union-attr]


def test_read_csv_parses_header_and_rows() -> None:
    rows = read_csv("q,lang\ndog,en\nねこ,ja\n")
    assert rows == [{"q": "dog", "lang": "en"}, {"q": "ねこ", "lang": "ja"}]


def test_data_and_datafile_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        load_scenarios(
            "- name: s\n  data: [{q: a}]\n  dataFile: x.csv\n  steps:\n    - tap: { id: x }\n"
        )


def test_row_substitution_reaches_preconditions() -> None:
    scns = load_scenarios(
        """
- name: s
  data:
    - { loc: ja_JP }
  preconditions:
    locale: "${row.loc}"
  steps:
    - tap: { id: x }
"""
    )
    out = expand_data(scns, _no_csv)
    assert out[0].preconditions.locale == "ja_JP"

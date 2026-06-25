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


def test_row_substitution_into_multiple_fields_stays_fully_typed() -> None:
    # A single row that interpolates into several distinct fields (and several distinct steps)
    # must still yield a fully-typed Scenario — nested models built, not raw dicts left behind.
    scns = load_scenarios(
        """
- name: multi
  data:
    - { q: dog, target: btn.go, loc: en_US }
  preconditions:
    locale: "${row.loc}"
  steps:
    - type: { into: { id: search.field }, text: "${row.q}", submit: true }
    - tap: { id: "${row.target}" }
  expect:
    - value: { sel: { id: out }, equals: "${row.q}" }
"""
    )
    out = expand_data(scns, _no_csv)
    assert len(out) == 1
    s = out[0]
    # Every substituted field carries through, and nested models are real objects (not raw dicts).
    assert s.preconditions.locale == "en_US"
    assert s.steps[0].type is not None and s.steps[0].type.text == "dog"
    assert s.steps[1].tap is not None and s.steps[1].tap.id == "btn.go"
    assert s.expect[0].value is not None and s.expect[0].value.sel.id == "out"
    assert s.expect[0].value.equals == "dog"
    assert s.data is None and s.data_file is None


def test_token_bearing_rows_are_revalidated() -> None:
    # The token-bearing path keeps the full re-validation: each derived instance is built via
    # Scenario.model_validate, so model-level invariants (here: a selector with no condition)
    # are enforced on every row exactly as before, not bypassed by an unchecked construct.
    scns = load_scenarios(
        """
- name: guarded
  data:
    - { sub: within }
  steps:
    - tap: { idMatches: "btn.${row.sub}" }
  expect:
    - exists: { id: "panel.${row.sub}" }
"""
    )
    out = expand_data(scns, _no_csv)
    assert len(out) == 1
    s = out[0]
    assert s.steps[0].tap is not None and s.steps[0].tap.id_matches == "btn.within"
    assert s.expect[0].exists is not None and s.expect[0].exists.sel.id == "panel.within"


def test_data_scenario_without_row_tokens_expands_per_row() -> None:
    # A data scenario whose body references no ${row.*} token still produces one fully-typed
    # instance per row, with the row name set and the data source cleared.
    scns = load_scenarios(
        """
- name: smoke
  data:
    - { unused: a }
    - { unused: b }
  steps:
    - tap: { id: x }
"""
    )
    out = expand_data(scns, _no_csv)
    assert len(out) == 2
    assert out[0].name == "smoke [row 1: unused=a]"
    assert out[1].name == "smoke [row 2: unused=b]"
    assert all(s.data is None and s.data_file is None for s in out)
    assert all(s.steps[0].tap is not None and s.steps[0].tap.id == "x" for s in out)


def test_row_instances_do_not_share_nested_models() -> None:
    # Each row is its own freshly-validated scenario, so per-scenario mutation downstream (e.g.
    # `run` sets preconditions.erase / launch_env per scenario) cannot leak across rows of the
    # same source — the shared base dict is consumed by independent model_validate calls.
    scns = load_scenarios(
        """
- name: iso
  data:
    - { unused: a }
    - { unused: b }
  steps:
    - tap: { id: x }
"""
    )
    out = expand_data(scns, _no_csv)
    assert out[0].preconditions is not out[1].preconditions
    out[0].preconditions.launch_env["k"] = "v"
    assert out[1].preconditions.launch_env == {}

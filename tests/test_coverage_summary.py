"""Tests for the floor the CI job summary renders against (scripts/coverage_summary.py, BE-0385).

The summary is presentation only, so it must never fail the step it runs in. What it must also not
do is mark a bar different from the one the gate enforced: BE-0385 gave both a single home in
``pyproject.toml``, and these tests pin that the summary reads that home and degrades quietly when
it cannot.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "coverage_summary.py"
_spec = importlib.util.spec_from_file_location("coverage_summary", _MODULE_PATH)
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cs
_spec.loader.exec_module(cs)


def test_the_floor_comes_from_the_key_the_gate_enforces(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text("[tool.coverage.report]\nfail_under = 91\n")
    assert cs.resolve_floor(path) == 91.0


def test_an_absent_floor_degrades_instead_of_failing_the_report(tmp_path: Path) -> None:
    # A report rendered against a slightly wrong bar still beats no report at all, which is why
    # this reader falls back where scripts/coverage_drift.py deliberately raises.
    path = tmp_path / "pyproject.toml"
    path.write_text("[tool.coverage.run]\nbranch = true\n")
    assert cs.resolve_floor(path) == cs.FALLBACK_FLOOR
    assert cs.resolve_floor(tmp_path / "absent.toml") == cs.FALLBACK_FLOOR


def test_the_repository_floor_is_read_not_guessed() -> None:
    root = Path(__file__).resolve().parent.parent
    assert cs.resolve_floor(root / "pyproject.toml") != cs.FALLBACK_FLOOR

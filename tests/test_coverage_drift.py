"""Tests for the floor-drift advisory (scripts/coverage_drift.py, BE-0385).

The advisory's whole value is that it nags without blocking, so the tests pin both halves: the gap
that makes it speak, and the fact that speaking never costs a nonzero exit. They also pin the one
asymmetry that placement in `make lint-pr` buys — a real input problem still fails, instead of being
swallowed the way a non-blocking step inside `make check` would have to swallow it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "coverage_drift.py"
_spec = importlib.util.spec_from_file_location("coverage_drift", _MODULE_PATH)
assert _spec and _spec.loader
cd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cd
_spec.loader.exec_module(cd)


def _coverage(path: Path, percent: float) -> Path:
    path.write_text(json.dumps({"totals": {"percent_covered": percent}}))
    return path


def _pyproject(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_no_reminder_while_the_gap_is_within_the_threshold() -> None:
    assert cd.message(89.0, 89.0) is None
    # Exactly at the threshold stays quiet — the reminder fires once the gap *passes* two points.
    assert cd.message(91.0, 89.0) is None


def test_reminder_names_both_numbers_and_the_gap() -> None:
    reminder = cd.message(93.45, 89.0)
    assert reminder is not None
    assert "93.45%" in reminder and "89.00%" in reminder and "4.45 points" in reminder
    assert "fail_under" in reminder


def test_read_floor_takes_the_key_the_gate_enforces(tmp_path: Path) -> None:
    path = _pyproject(tmp_path / "pyproject.toml", "[tool.coverage.report]\nfail_under = 89\n")
    assert cd.read_floor(path) == 89.0


def test_drift_is_advisory_and_never_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = _coverage(tmp_path / "coverage.json", 93.45)
    pyproject = _pyproject(tmp_path / "pyproject.toml", "[tool.coverage.report]\nfail_under = 89\n")

    assert cd.main(["--coverage", str(coverage), "--pyproject", str(pyproject)]) == 0
    out = capsys.readouterr().out
    assert "advisory, not failing" in out
    assert "4.45 points" in out


def test_a_narrow_gap_reports_the_numbers_without_a_reminder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = _coverage(tmp_path / "coverage.json", 90.0)
    pyproject = _pyproject(tmp_path / "pyproject.toml", "[tool.coverage.report]\nfail_under = 89\n")

    assert cd.main(["--coverage", str(coverage), "--pyproject", str(pyproject)]) == 0
    assert "no drift to report" in capsys.readouterr().out


def test_a_missing_coverage_report_is_a_notice_not_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `make lint-pr` is run before pushing, with or without a prior `make test`.
    assert cd.main(["--coverage", str(tmp_path / "absent.json")]) == 0
    assert "run `make test`" in capsys.readouterr().out


def test_an_unreadable_coverage_report_fails_instead_of_being_swallowed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = tmp_path / "coverage.json"
    coverage.write_text("{ not json")
    assert cd.main(["--coverage", str(coverage)]) == 1
    assert "unreadable coverage report" in capsys.readouterr().err


def test_a_floor_that_is_gone_from_pyproject_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # BE-0385 moved the floor into pyproject.toml precisely so there is one source; if that source
    # disappears, the advisory has nothing to compare against and must say so.
    coverage = _coverage(tmp_path / "coverage.json", 93.0)
    pyproject = _pyproject(tmp_path / "pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
    assert cd.main(["--coverage", str(coverage), "--pyproject", str(pyproject)]) == 1
    assert "no usable floor" in capsys.readouterr().err


def test_the_repository_floor_is_declared_where_both_readers_look() -> None:
    """The gate, this advisory, and the CI summary all read this one key — it must exist."""
    root = Path(__file__).resolve().parent.parent
    assert cd.read_floor(root / "pyproject.toml") > 0


def _throwaway_package(tmp_path: Path, floor: int) -> Path:
    """A one-file package at 75% branch coverage, beside a pyproject declaring `floor`."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text(
        "def f(x):\n    if x:\n        return 1\n    return 2\n"
    )
    (tmp_path / "test_f.py").write_text(
        "from pkg import f\n\n\ndef test_f():\n    assert f(True) == 1\n"
    )
    (tmp_path / "pyproject.toml").write_text(f"[tool.coverage.report]\nfail_under = {floor}\n")
    return tmp_path


@pytest.mark.parametrize(("floor", "expected"), [(99, 1), (50, 0)])
def test_pytest_cov_still_enforces_the_floor_pyproject_declares(
    tmp_path: Path, floor: int, expected: int
) -> None:
    """Pin the pytest-cov behaviour BE-0385's total floor now rests on.

    Dropping `--cov-fail-under` from the `Makefile` moved enforcement onto pytest-cov adopting
    `[tool.coverage.report]`'s `fail_under` when the flag is absent. Nothing else in the suite
    pins that: a dependency bump that stopped adopting the key would leave `make test` passing at
    any coverage with nothing going red. This runs a throwaway package in its own directory, so it
    reads that package's pyproject rather than this repository's.
    """
    root = _throwaway_package(tmp_path, floor)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--cov=pkg",
            "--cov-report=",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr

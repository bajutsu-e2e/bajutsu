"""Tests for the per-file coverage ratchet (scripts/coverage_floors.py, BE-0385).

The point of the snapshot is that it only ever moves *up* on its own: a drop fails the gate, a rise
passes without anyone touching the file, and the snapshot is rewritten only by the deliberate
`--write` run. These tests pin each of those, plus the two shapes that must not fail — a file with
no floor yet, and a floor whose file is no longer measured.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "coverage_floors.py"
_spec = importlib.util.spec_from_file_location("coverage_floors", _MODULE_PATH)
assert _spec and _spec.loader
cf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cf
_spec.loader.exec_module(cf)


def _report(files: dict[str, tuple[float, int]]) -> dict[str, Any]:
    """A coverage.py JSON report carrying just the fields the script reads."""
    return {
        "files": {
            name: {"summary": {"percent_covered": percent, "num_statements": statements}}
            for name, (percent, statements) in files.items()
        },
        "totals": {"percent_covered": 90.0},
    }


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload))
    return path


def _snapshot(path: Path, floors: dict[str, float]) -> Path:
    path.write_text(json.dumps({"min_statements": cf.MIN_STATEMENTS, "files": floors}))
    return path


def test_truncate_never_rounds_a_floor_above_what_was_measured() -> None:
    # Rounding 65.999 up to 66.0 would record a floor the very run that produced it falls short of.
    assert cf.truncate(65.999) == 65.99
    assert cf.truncate(100.0) == 100.0
    assert cf.truncate(0.0) == 0.0


def test_measured_skips_files_below_the_statement_threshold() -> None:
    # `bajutsu/__main__.py`'s four never-imported lines are the case this excludes: a floor there
    # would swing by 25 points per statement.
    data = _report({"big.py": (80.0, cf.MIN_STATEMENTS), "tiny.py": (0.0, cf.MIN_STATEMENTS - 1)})
    assert cf.measured(data) == {"big.py": 80.0}


def test_compare_reports_a_drop_and_stays_quiet_on_a_rise() -> None:
    drops, notes = cf.compare({"a.py": 79.99, "b.py": 91.0}, {"a.py": 80.0, "b.py": 90.0})
    assert len(drops) == 1
    assert "a.py" in drops[0]
    assert notes == []


def test_compare_notes_but_never_fails_an_unrecorded_or_vanished_file() -> None:
    drops, notes = cf.compare({"new.py": 50.0}, {"gone.py": 90.0})
    assert drops == []
    assert notes == [
        "gone.py: recorded floor but no longer measured",
        "new.py: no recorded floor yet",
    ]


def test_check_fails_on_a_drop_and_names_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = _write(tmp_path / "coverage.json", _report({"a.py": (70.0, 40)}))
    snapshot = _snapshot(tmp_path / "floors.json", {"a.py": 80.0})

    assert cf.main(["--coverage", str(coverage), "--snapshot", str(snapshot)]) == 1
    err = capsys.readouterr().err
    assert "a.py" in err
    assert "70.00%" in err and "80.00%" in err
    assert "make coverage-floors" in err
    # Check-only: the failing run must not have moved the bar it just enforced.
    assert json.loads(snapshot.read_text())["files"] == {"a.py": 80.0}


def test_check_passes_when_coverage_rose_without_the_snapshot_moving(tmp_path: Path) -> None:
    coverage = _write(tmp_path / "coverage.json", _report({"a.py": (95.0, 40)}))
    snapshot = _snapshot(tmp_path / "floors.json", {"a.py": 80.0})
    assert cf.main(["--coverage", str(coverage), "--snapshot", str(snapshot)]) == 0


def test_check_fails_when_the_snapshot_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Deleting the snapshot must not read as "every file passes" — there would be no floor at all.
    coverage = _write(tmp_path / "coverage.json", _report({"a.py": (10.0, 40)}))
    assert cf.main(["--coverage", str(coverage), "--snapshot", str(tmp_path / "absent.json")]) == 1
    assert "unenforceable" in capsys.readouterr().err


def test_check_notes_a_file_the_snapshot_has_not_recorded_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = _write(tmp_path / "coverage.json", _report({"a.py": (10.0, 40), "b.py": (95.0, 40)}))
    snapshot = _snapshot(tmp_path / "floors.json", {"a.py": 10.0})
    assert cf.main(["--coverage", str(coverage), "--snapshot", str(snapshot)]) == 0
    assert "b.py: no recorded floor yet" in capsys.readouterr().out


def test_write_seeds_a_snapshot_that_does_not_exist_yet(tmp_path: Path) -> None:
    coverage = _write(tmp_path / "coverage.json", _report({"a.py": (88.0, 40)}))
    snapshot = tmp_path / "floors.json"
    assert cf.main(["--coverage", str(coverage), "--snapshot", str(snapshot), "--write"]) == 0
    assert json.loads(snapshot.read_text())["files"] == {"a.py": 88.0}


def test_write_rewrites_the_snapshot_and_reports_rises_and_drops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = _write(
        tmp_path / "coverage.json",
        _report({"up.py": (95.0, 40), "down.py": (60.0, 40), "new.py": (88.0, 40)}),
    )
    snapshot = _snapshot(
        tmp_path / "floors.json", {"up.py": 80.0, "down.py": 70.0, "gone.py": 50.0}
    )

    assert cf.main(["--coverage", str(coverage), "--snapshot", str(snapshot), "--write"]) == 0

    written = json.loads(snapshot.read_text())
    assert written["files"] == {"up.py": 95.0, "down.py": 60.0, "new.py": 88.0}
    assert written["min_statements"] == cf.MIN_STATEMENTS

    out = capsys.readouterr().out
    assert "↑ up.py" in out
    # An accepted drop is called out by name, so a human sees it before committing the rewrite.
    assert "↓ down.py" in out and "accepted drop" in out
    assert "+ new.py" in out
    assert "- gone.py" in out


def test_write_leaves_a_stable_diff(tmp_path: Path) -> None:
    snapshot = tmp_path / "floors.json"
    cf.save_snapshot(snapshot, {"b.py": 90.0, "a.py": 80.0})
    text = snapshot.read_text()
    assert text.endswith("\n")
    assert text.index('"a.py"') < text.index('"b.py"')
    assert cf.load_snapshot(snapshot) == {"a.py": 80.0, "b.py": 90.0}


def test_missing_coverage_report_fails_with_an_actionable_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cf.main(["--coverage", str(tmp_path / "absent.json")]) == 1
    assert "make test" in capsys.readouterr().err


@pytest.mark.parametrize("payload", ["{ not json", '{"files": {"a.py": {}}}'])
def test_unreadable_coverage_report_fails_rather_than_passing_quietly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: str
) -> None:
    coverage = tmp_path / "coverage.json"
    coverage.write_text(payload)
    assert cf.main(["--coverage", str(coverage), "--snapshot", str(tmp_path / "absent.json")]) == 1
    assert "unreadable coverage report" in capsys.readouterr().err


def test_unreadable_snapshot_fails_rather_than_seeding_an_empty_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = _write(tmp_path / "coverage.json", _report({"a.py": (70.0, 40)}))
    snapshot = tmp_path / "floors.json"
    snapshot.write_text('{"files": "not a mapping"}')
    assert cf.main(["--coverage", str(coverage), "--snapshot", str(snapshot)]) == 1
    assert "unreadable snapshot" in capsys.readouterr().err


def test_committed_snapshot_matches_the_scripts_own_format() -> None:
    """The tracked snapshot must stay loadable by the gate that reads it on every run."""
    snapshot = Path(__file__).resolve().parent.parent / "coverage-floors.json"
    data = json.loads(snapshot.read_text())
    assert data["min_statements"] == cf.MIN_STATEMENTS
    assert data["files"]
    assert all(0.0 <= value <= 100.0 for value in data["files"].values())

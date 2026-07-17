"""GitHub Actions integration: failure annotations + the job summary."""

from __future__ import annotations

from pathlib import Path

from bajutsu.github import actions
from bajutsu.orchestrator import RunResult


def _res(name: str, ok: bool, failure: str | None = None) -> RunResult:
    return RunResult(scenario=name, ok=ok, steps=[], failure=failure)


def test_emit_noop_outside_actions(tmp_path: Path) -> None:
    out: list[str] = []
    emitted = actions.emit(
        [_res("s", False, "x")], tmp_path / "report.html", env={}, echo=out.append
    )
    assert emitted is False and out == []  # nothing outside Actions


def test_emit_annotations_and_summary(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    out: list[str] = []
    results = [_res("login works", True), _res("checkout fails", False, "value mismatch\nactual=3")]
    emitted = actions.emit(
        results,
        Path("runs/r1/report.html"),
        env={"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)},
        echo=out.append,
    )
    assert emitted is True
    # One annotation per failure, single-line, titled by scenario.
    assert out == ["::error title=bajutsu: checkout fails::value mismatch actual=3"]
    # Summary: a verdict header + a row per scenario.
    text = summary.read_text(encoding="utf-8")
    assert "## bajutsu — FAIL (1/2)" in text
    assert "| ✅ | login works |" in text
    assert "| ❌ | checkout fails | value mismatch actual=3 |" in text
    assert "runs/r1/report.html" in text


def test_emit_summary_appends(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("pre-existing\n", encoding="utf-8")
    env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}
    actions.emit([_res("s", True)], Path("report.html"), env=env, echo=lambda _: None)
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("pre-existing\n")  # appended, not overwritten
    assert "## bajutsu — PASS (1/1)" in text

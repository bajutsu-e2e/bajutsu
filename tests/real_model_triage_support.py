"""Shared support for the triage real-model tests (BE-0296).

Both `test_real_model_triage_smoke.py` (does a real diagnosis response parse today?) and
`test_real_model_triage_fixtures.py` (capture a real diagnosis once, replay it forever) ask the same
question — does a genuine model's diagnosis parse into the `Triage` schema, and is a proposed fix
well-formed — so the failure-context builder and the parse-validity assertion live here as the one
source of truth. The generic capture harness (`RecordingBackend`, `save_fixture`, `load_fixture`)
and the credential gate are reused from `real_model_support` (BE-0295); a forced `diagnose` turn is
one tool-use block, exactly the shape that harness replays.

Nothing here runs a model; the live callers pass a real backend in. No LLM ever touches the
`run` / CI verdict (prime directive 1) — triage is advisory (BE-0104 / DESIGN.md M4), and this
verifies only that its output parses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Re-exported so the triage tests import from one place. The `as` form is what makes it an explicit
# re-export under strict mypy, which otherwise treats an imported name as private to this module.
from real_model_support import RecordingBackend as RecordingBackend
from real_model_support import load_fixture as load_fixture
from real_model_support import requires_credential as requires_credential
from real_model_support import save_fixture as save_fixture
from real_model_support import showcase_screen as showcase_screen

from bajutsu.common.agents.claude_triage import _CATEGORIES, NO_DIAGNOSIS_SUMMARY
from bajutsu.triage.heuristic import FIX_KINDS, FailedStep, Triage, TriageContext

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "be0296"

# `_to_triage` returns this exact summary when the response carried no tool-use block — i.e. the
# diagnosis failed to parse. Imported from the product rather than copied so the two stay in lock
# step: asserting the real result is NOT this sentinel is what proves a live run genuinely validated
# a parsed diagnosis rather than passing on an empty fallback, and a copy could silently drift.
_NO_DIAGNOSIS = NO_DIAGNOSIS_SUMMARY

# A realistic selector-rename failure over the committed showcase "controls" screen: the scenario
# taps `log.intens` (a typo), while the real screen exposes `log.intense`. A real model reasoning
# over the element tree should diagnose a `selector` root cause and, when confident, propose a
# `renameId` fix — exercising both the diagnosis-parsing and the fix-parsing paths.
_TARGET_TYPO = "log.intens"
_SCENARIO_YAML = f"- name: controls\n  steps:\n    - tap: {{ id: {_TARGET_TYPO} }}\n"


def diagnose_payload() -> dict[str, Any]:
    """A `diagnose` tool input matching `triage_context`'s selector-rename failure.

    The one expected diagnosis shape the smoke and fixtures self-checks both drive a `FakeBackend`
    with, so a change to what a parsed diagnosis should look like is edited in one place.
    """
    return {
        "category": "selector",
        "summary": "the step taps log.intens but the screen exposes log.intense",
        "suggestions": ["did you mean log.intense?"],
        "fix": {"kind": "renameId", "find": _TARGET_TYPO, "replace": "log.intense"},
    }


def triage_context() -> TriageContext:
    """A failed-scenario context over a committed showcase golden — a real screen, no Simulator."""
    return TriageContext(
        scenario="controls",
        failure=f"step0 tap: 一致なし: {_TARGET_TYPO}",
        failed_step=FailedStep(0, "tap", f"一致なし: {_TARGET_TYPO}"),
        failed_expectations=[],
        elements=showcase_screen("controls"),
        scenario_yaml=_SCENARIO_YAML,
        target_id=_TARGET_TYPO,
        evidence=["deviceLog"],
    )


def assert_parses_to_triage(result: Triage) -> None:
    """The real diagnosis mapped to a well-formed `Triage`, not the empty no-diagnosis fallback.

    A forced `diagnose` call over `triage_context`'s deliberately unambiguous selector-rename
    failure must yield a concrete diagnosis: a non-empty summary that is not the no-tool-use
    sentinel, and a category the model actually classified rather than punted on. Asserting the
    category is not "unknown" carries real signal precisely because `_to_triage` clamps *any*
    category outside the diagnose enum to "unknown" — so this one check catches both a model that
    failed to classify and a category that did not map into the enum. When the model proposes a fix,
    `_parse_fix` has already validated its shape — assert it here too, so the check documents the
    contract a real fix must satisfy (a known kind, a non-trivial `find` -> `replace`).
    """
    assert result.summary and result.summary != _NO_DIAGNOSIS, (
        f"real diagnosis did not parse into a Triage summary: {result}"
    )
    assert result.category in _CATEGORIES and result.category != "unknown", (
        f"real diagnosis did not classify the failure into a concrete category: {result.category}"
    )
    if result.fix is not None:
        assert result.fix.kind in FIX_KINDS, f"proposed fix has an unknown kind: {result.fix.kind}"
        assert result.fix.find and result.fix.replace and result.fix.find != result.fix.replace, (
            f"proposed fix is not a well-formed find -> replace: {result.fix}"
        )

"""Unit tests for the mechanical PR-metadata checks (BE-0069 D, `scripts/lint_pr.py`).

The pure helpers take plain lists/strings so they test without any real git — the thin ``main()``
that gathers git data is out of scope here (no subprocess in tests). One assertion per behaviour.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the script as a module (matching tests/test_lint_roadmap.py) rather than tweaking sys.path,
# so there's no module-level import-after-statement to suppress.
_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lint_pr.py"
_spec = importlib.util.spec_from_file_location("lint_pr", _MODULE_PATH)
assert _spec and _spec.loader
lp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lp
_spec.loader.exec_module(lp)

bad_commit_subjects = lp.bad_commit_subjects
behavior_without_test = lp.behavior_without_test
title_prefix_problem = lp.title_prefix_problem
touches_roadmap = lp.touches_roadmap


def test_bad_commit_subjects_accepts_scoped_conventional_subjects() -> None:
    subjects = [
        "feat(run): add condition wait",
        "fix(record): handle empty selector",
        "docs(drivers): migrate to Google-style",
        "chore(ci): bump actionlint",
        "refactor(scenario): extract helper",
        "test(network): cover redirects",
        "perf(runner): cache compiled selectors",
        "ci(workflows): add cache",
    ]
    assert bad_commit_subjects(subjects) == []


def test_bad_commit_subjects_allows_docs_without_scope() -> None:
    assert bad_commit_subjects(["docs: clarify the gate"]) == []


def test_bad_commit_subjects_allows_multi_word_scope_with_separators() -> None:
    # The scope charset permits comma, underscore and dash (e.g. a multi-area scope).
    assert bad_commit_subjects(["feat(run,record): shared helper", "fix(web_ui): typo"]) == []


def test_bad_commit_subjects_flags_unscoped_non_docs_type() -> None:
    # feat/fix/etc. require a scope; only docs may omit it.
    assert bad_commit_subjects(["feat: no scope here"]) == ["feat: no scope here"]


def test_bad_commit_subjects_flags_unknown_type() -> None:
    assert bad_commit_subjects(["wip(run): scratch"]) == ["wip(run): scratch"]


def test_bad_commit_subjects_flags_missing_colon_and_description() -> None:
    assert bad_commit_subjects(["feat(run)"]) == ["feat(run)"]
    assert bad_commit_subjects(["feat(run): "]) == ["feat(run): "]


def test_bad_commit_subjects_flags_whitespace_only_description() -> None:
    # A description of only spaces (more than one, so the colon-space isn't the whole tail) must
    # still be rejected — the subject needs a real, non-blank description.
    assert bad_commit_subjects(["feat(run):   "]) == ["feat(run):   "]
    assert bad_commit_subjects(["docs:  "]) == ["docs:  "]


def test_bad_commit_subjects_returns_only_the_bad_ones_in_order() -> None:
    subjects = ["feat(run): ok", "broken subject", "docs: ok", "WIP"]
    assert bad_commit_subjects(subjects) == ["broken subject", "WIP"]


def test_touches_roadmap_true_when_a_roadmap_path_changed() -> None:
    paths = ["bajutsu/runner.py", "roadmaps/proposals/BE-0099-x/BE-0099-x.md"]
    assert touches_roadmap(paths) is True


def test_touches_roadmap_false_without_roadmap_paths() -> None:
    assert touches_roadmap(["bajutsu/runner.py", "tests/test_runner.py"]) is False


def test_behavior_without_test_true_when_core_python_changed_but_no_tests() -> None:
    paths = ["bajutsu/runner.py", "bajutsu/drivers/base.py"]
    assert behavior_without_test(paths) is True


def test_behavior_without_test_false_when_tests_changed_alongside() -> None:
    paths = ["bajutsu/runner.py", "tests/test_runner.py"]
    assert behavior_without_test(paths) is False


def test_behavior_without_test_false_for_docs_only_python_change() -> None:
    # A docs-only change (.md) with no bajutsu/ Python change is not a behaviour change.
    assert behavior_without_test(["docs/ai-development.md", "README.md"]) is False


def test_behavior_without_test_ignores_test_files_under_bajutsu() -> None:
    # bajutsu's own test_*.py shouldn't count as a behaviour change needing a test delta.
    assert behavior_without_test(["bajutsu/runner_test.py"]) is False


def test_behavior_without_test_false_for_non_python_core_change() -> None:
    assert behavior_without_test(["bajutsu/py.typed"]) is False


def test_title_prefix_problem_none_when_not_a_roadmap_change() -> None:
    assert title_prefix_problem("feat(run): add wait", touches_roadmap_=False) is None


def test_title_prefix_problem_none_for_well_prefixed_roadmap_title() -> None:
    assert title_prefix_problem("[BE-0069] feat(dev): lint-pr", touches_roadmap_=True) is None


def test_title_prefix_problem_accepts_be_placeholder_prefix() -> None:
    assert title_prefix_problem("[BE-XXXX] docs: scaffold", touches_roadmap_=True) is None


def test_title_prefix_problem_flags_roadmap_title_missing_prefix() -> None:
    assert title_prefix_problem("feat(dev): lint-pr", touches_roadmap_=True) is not None


def test_title_prefix_problem_none_when_title_missing() -> None:
    # No PR title available locally — nothing to validate (the reminder path handles that).
    assert title_prefix_problem(None, touches_roadmap_=True) is None

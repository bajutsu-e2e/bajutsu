#!/usr/bin/env python3
"""Check the *mechanical* PR-metadata conventions on the current branch (BE-0069 D).

The contributor conventions in [`CLAUDE.md`](../CLAUDE.md) that are purely **mechanical** — and so
can be checked without judgement — made into one command a human and an AI run identically::

    make lint-pr

It compares the branch against ``origin/main`` and reports three things:

- **Scoped commit subjects** — each commit subject must be a conventional, scoped subject
  (``feat(scope): …`` / ``fix(scope): …`` / ``docs: …``). A non-conforming subject is a clear
  violation.
- **Roadmap PR title prefix** — when the diff touches ``roadmaps/``, the PR title must carry a
  ``[BE-NNNN]`` (or ``[BE-XXXX]``) prefix. The PR title is not available locally, so locally this is
  an advisory **reminder**; in CI (where ``$PR_TITLE`` is set) the real title is validated and a
  missing prefix is a violation.
- **Behavior-change-without-test reminder** — if the diff changes non-test Python under
  ``bajutsu/`` but ``tests/`` is unchanged, a reminder to add or adjust a test.

By design it never blocks on the **un-mechanizable** rules ("stay in your lane", bilingual prose
style) — those stay prose and reviewer judgement. It is **advisory / opt-in**, deliberately *not* in
``make check``: it needs PR/branch context (the gate runs anywhere, on any checkout) and the
test-delta and local roadmap-title items are reminders, not gates.

**Exit code.** Nonzero only on a *clear mechanical violation* — a non-scoped commit subject, or (in
CI, with ``$PR_TITLE``) a roadmap PR whose title lacks the ``[BE-NNNN]`` prefix — so the command is
useful when wired into a check. The behavior-without-test reminder and the *local* roadmap-title
reminder never fail on their own.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# A conventional, scoped commit subject. The scope charset (lower-case words, digits, and
# ``,_-``) covers a multi-area scope like ``feat(run,record): …``. ``docs`` may omit the scope
# (``docs: …``), matching CLAUDE.md's stated convention; every other type requires one.
_SUBJECT_RE = re.compile(
    r"^(?:feat|fix|chore|ci|refactor|test|perf)\([a-z0-9,_-]+\): \S.*$|^docs(?:\([a-z0-9,_-]+\))?: \S.*$"
)

# A PR title carrying the roadmap prefix: ``[BE-NNNN]`` (a real id) or the ``[BE-XXXX]`` placeholder
# CI replaces. Anchored at the start so the prefix must lead the title.
_TITLE_PREFIX_RE = re.compile(r"^\[BE-(?:\d{4}|XXXX)\] ")


def bad_commit_subjects(subjects: list[str]) -> list[str]:
    """The commit subjects, in order, that are not conventional scoped subjects.

    Args:
        subjects: One commit subject line per element (e.g. ``git log --format=%s``).

    Returns:
        The subset that does not match the conventional-commit shape, preserving input order.
    """
    return [s for s in subjects if not _SUBJECT_RE.match(s)]


def touches_roadmap(paths: list[str]) -> bool:
    """Whether the changed paths include any file under ``roadmaps/``."""
    return any(p.startswith("roadmaps/") for p in paths)


def behavior_without_test(paths: list[str]) -> bool:
    """Whether the diff changes core Python under ``bajutsu/`` with no ``tests/`` delta.

    A behaviour change to the logic core should normally carry a test change. This flags the
    *mechanical* signal of that — a non-test ``bajutsu/*.py`` file changed while ``tests/`` did
    not — as a reminder, not a gate. ``bajutsu``'s own ``*_test.py`` / ``test_*.py`` files don't
    count as a behaviour change.
    """

    def is_core_python(p: str) -> bool:
        if not (p.startswith("bajutsu/") and p.endswith(".py")):
            return False
        name = p.rsplit("/", 1)[-1]
        return not (name.startswith("test_") or name.endswith("_test.py"))

    changed_core = any(is_core_python(p) for p in paths)
    changed_tests = any(p.startswith("tests/") for p in paths)
    return changed_core and not changed_tests


def title_prefix_problem(title: str | None, *, touches_roadmap_: bool) -> str | None:
    """A message when a roadmap PR's title lacks the ``[BE-NNNN]`` prefix, else None.

    Returns None when the change does not touch ``roadmaps/`` (no prefix required) or when no title
    is available (``title is None`` — locally the title isn't known, so there is nothing to
    validate; the reminder path covers that case instead).
    """
    if not touches_roadmap_ or title is None:
        return None
    if _TITLE_PREFIX_RE.match(title):
        return None
    return f"PR title must start with a [BE-NNNN] (or [BE-XXXX]) prefix for a roadmap change: {title!r}"


def _git_lines(args: list[str]) -> list[str]:
    """Run a git command and return its nonempty stdout lines.

    Exits with a clear message if git fails (e.g. no `origin/main`): a silent empty list would
    print a false "no advisories" without having checked anything.
    """
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"lint-pr: `git {' '.join(args)}` failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    # `A..B` lists commits on the branch but not on origin/main; `A...B` diffs against the merge
    # base, so a stale origin/main doesn't show others' merged changes as ours.
    subjects = _git_lines(["log", "--format=%s", "origin/main..HEAD"])
    paths = _git_lines(["diff", "--name-only", "origin/main...HEAD"])

    on_roadmap = touches_roadmap(paths)
    pr_title = os.environ.get("PR_TITLE")  # set in CI; absent locally

    violations: list[str] = []
    reminders: list[str] = []

    bad = bad_commit_subjects(subjects)
    if bad:
        violations.append("non-scoped commit subject(s) (want `type(scope): …`, or `docs: …`):")
        violations.extend(f"    {s}" for s in bad)

    if on_roadmap:
        problem = title_prefix_problem(pr_title, touches_roadmap_=on_roadmap)
        if problem is not None:
            violations.append(problem)  # only reachable when PR_TITLE is set (CI)
        elif pr_title is None:
            reminders.append(
                "this change touches roadmaps/ — the PR title must carry a [BE-NNNN] "
                "(or [BE-XXXX]) prefix."
            )

    if behavior_without_test(paths):
        reminders.append(
            "bajutsu/ Python changed but tests/ did not — add or adjust a test for the behaviour."
        )

    if violations:
        print("lint-pr: mechanical violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
    if reminders:
        # Reminders go to stdout (advisory, not a failure) so they're visible without being noise
        # on stderr that a wrapping check might treat as an error.
        print("lint-pr: reminder(s) (advisory, not failing):")
        for r in reminders:
            print(f"  {r}")
    if not violations and not reminders:
        print("lint-pr: commit subjects scoped; no advisories.")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

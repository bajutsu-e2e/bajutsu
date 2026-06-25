#!/usr/bin/env python3
"""Check the *mechanical* PR-metadata conventions on the current branch (BE-0069 D).

The contributor conventions in [`CLAUDE.md`](../CLAUDE.md) that are purely **mechanical** — and so
can be checked without judgement — made into one command a human and an AI run identically::

    make lint-pr

It compares the branch against ``origin/main`` and reports:

- **Scoped commit subjects** — each commit subject must be a conventional, scoped subject
  (``feat(scope): …`` / ``fix(scope): …`` / ``docs: …``). A non-conforming subject is a clear
  violation.
- **PR title** — a scoped conventional subject, and — when the branch name encodes a roadmap id
  (``claude/be-0050-<slug>``) — the matching ``[BE-0050]`` prefix. The branch, not the diff, is the
  authoritative id signal, so a missing or mismatched id is caught. The PR title is not available
  locally, so locally this is an advisory **reminder**; in CI (where ``$PR_TITLE`` is set) the real
  title is validated and a violation fails the check.
- **Behavior-change-without-test reminder** — if the diff changes non-test Python under
  ``bajutsu/`` but ``tests/`` is unchanged, a reminder to add or adjust a test.

The ``--title-only`` mode validates just the PR title from ``$PR_TITLE`` and ``$HEAD_REF`` (no git
history needed); the ``pr-title`` workflow runs it on every PR. By design the command never blocks on
the **un-mechanizable** rules ("stay in your lane", bilingual prose style) — those stay prose and
reviewer judgement. The full ``make lint-pr`` is **advisory / opt-in**, deliberately *not* in
``make check``: it needs PR/branch context (the gate runs anywhere, on any checkout) and the
test-delta and local roadmap-title items are reminders, not gates.

**Exit code.** Nonzero only on a *clear mechanical violation* — a non-scoped commit subject, or (in
CI, with ``$PR_TITLE``) a malformed PR title or one whose roadmap-id prefix is missing/mismatched —
so the command is useful when wired into a check. The behavior-without-test reminder and the *local*
roadmap-title reminder never fail on their own.
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

# A BE id embedded in a branch name (the convention ``claude/be-0050-<slug>``). Case-insensitive,
# and the number is exactly four digits — the zero-padded roadmap id, not an arbitrary number.
_REF_BE_RE = re.compile(r"be-(\d{4})(?![0-9])", re.IGNORECASE)


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


def be_id_from_ref(ref: str) -> str | None:
    """The canonical ``BE-NNNN`` a branch name encodes (``claude/be-0050-…`` → ``BE-0050``), or None.

    The branch convention ``claude/be-NNNN-<slug>`` ties a PR to a roadmap item, so the branch — not
    the diff — is the authoritative signal that the PR title must carry that id. Matching is
    case-insensitive; a non-roadmap topic branch yields None.
    """
    m = _REF_BE_RE.search(ref)
    return f"BE-{m.group(1)}" if m else None


def pr_title_problems(title: str, branch_be_id: str | None) -> list[str]:
    """The PR-title violations, in order — empty when the title is well-formed.

    Two mechanical checks the CI title gate enforces:

    - **Form** — stripped of an optional leading ``[BE-NNNN]`` / ``[BE-XXXX]`` prefix, the title must
      be a conventional scoped subject (``feat(scope): …`` / ``docs: …``), same shape as a commit
      subject.
    - **Roadmap id** — when ``branch_be_id`` is set (the branch encodes a roadmap id), the title must
      lead with exactly that ``[BE-NNNN]`` prefix, catching both a missing prefix and one copied from
      another item.
    """
    problems: list[str] = []
    prefix = _TITLE_PREFIX_RE.match(title)
    body = title[prefix.end() :] if prefix else title
    if not _SUBJECT_RE.match(body):
        problems.append(
            f"PR title must be a scoped conventional subject (e.g. `feat(run): …`), "
            f"after any [BE-NNNN] prefix: {title!r}"
        )
    if branch_be_id is not None and not title.startswith(f"[{branch_be_id}] "):
        problems.append(
            f"branch encodes {branch_be_id}; PR title must start with '[{branch_be_id}] ': {title!r}"
        )
    return problems


def _git_lines(args: list[str]) -> list[str]:
    """Run a git command and return its nonempty stdout lines.

    Exits with a clear message if git fails (e.g. no `origin/main`): a silent empty list would
    print a false "no advisories" without having checked anything.
    """
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"lint-pr: `git {' '.join(args)}` failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def _title_only() -> int:
    """Validate only the PR title — the CI gate (`--title-only`); no git history needed.

    Reads ``PR_TITLE`` (the PR title) and ``HEAD_REF`` (the branch name) from the environment, as
    GitHub Actions sets them. Exits nonzero on any title violation so the workflow goes red.
    """
    pr_title = os.environ.get("PR_TITLE")
    if not pr_title:
        raise SystemExit("lint-pr --title-only: PR_TITLE is not set")
    branch_be_id = be_id_from_ref(os.environ.get("HEAD_REF", ""))
    problems = pr_title_problems(pr_title, branch_be_id)
    if problems:
        print("lint-pr: PR-title violation(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("lint-pr: PR title OK.")
    return 0


def main() -> int:
    if "--title-only" in sys.argv[1:]:
        return _title_only()

    # `A..B` lists commits on the branch but not on origin/main; `A...B` diffs against the merge
    # base, so a stale origin/main doesn't show others' merged changes as ours.
    subjects = _git_lines(["log", "--format=%s", "origin/main..HEAD"])
    paths = _git_lines(["diff", "--name-only", "origin/main...HEAD"])

    on_roadmap = touches_roadmap(paths)
    pr_title = os.environ.get("PR_TITLE")  # set in CI; absent locally
    # The branch — not the diff — is the authoritative roadmap-id signal (HEAD_REF in CI, else the
    # checked-out branch).
    head_ref = (
        os.environ.get("HEAD_REF") or (_git_lines(["rev-parse", "--abbrev-ref", "HEAD"]) or [""])[0]
    )
    branch_be_id = be_id_from_ref(head_ref)

    violations: list[str] = []
    reminders: list[str] = []

    bad = bad_commit_subjects(subjects)
    if bad:
        violations.append("non-scoped commit subject(s) (want `type(scope): …`, or `docs: …`):")
        violations.extend(f"    {s}" for s in bad)

    if pr_title is not None:
        # The CI title gate, mirrored here when the title is known: form + the branch's exact id.
        violations.extend(pr_title_problems(pr_title, branch_be_id))
    elif branch_be_id is not None:
        reminders.append(
            f"branch encodes {branch_be_id} — the PR title must start with '[{branch_be_id}] '."
        )

    # A roadmap diff on a branch that doesn't name the id still needs *some* [BE-NNNN] prefix
    # (e.g. a proposal-only PR); the branch-id check above can't cover it.
    if on_roadmap and branch_be_id is None:
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

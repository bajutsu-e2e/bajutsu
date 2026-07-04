#!/usr/bin/env python3
"""Decide whether a PR warrants the metered on-device E2E jobs (e2e.yml's `changes` job).

The macOS jobs (smoke, xcuitest) bill at 10x, so e2e.yml only fires them when the PR touches what
they actually exercise — the app, the SDK, the run path, the build/deps, or the E2E
workflow/action itself. This module is the single source of truth for that decision, split into two
testable pieces:

- ``changed_files`` lists the PR's *own* changes with a **three-dot** diff (``git diff base...head``,
  i.e. from the merge base of the two commits to ``head``). ``base`` is the base-branch tip, so a
  two-dot ``git diff base head`` compares the tips directly: when ``base`` has advanced past the
  PR's fork point it reports every file main touched meanwhile as "changed". An unrelated
  ``bajutsu/runner/…`` commit on main would then trip the filter and burn the metered jobs on, say,
  a roadmap-only PR. The merge-base diff yields only what the PR itself changed.

- ``is_relevant`` is the positive-list: each changed path is matched against the patterns the
  on-device jobs exercise. Subpackages are listed explicitly (runner, scenario, drivers,
  orchestrator); ``bajutsu/*.py`` covers the single-level modules in the run path while inherently
  excluding subpackages the E2E never touches (serve/, mcp/, templates/, report/). CLI commands are
  limited to the three entry points the E2E invokes. A new subpackage or CLI command defaults to
  NOT triggering — add its pattern here when it becomes on-device-relevant.

Invoked by the workflow with ``BASE_SHA`` / ``HEAD_SHA`` in the environment; it writes
``relevant=true|false`` to ``GITHUB_OUTPUT``. An empty ``BASE_SHA`` (a manual ``workflow_dispatch``
with no PR context) always counts as relevant.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable

# One path is enough to trigger; anchored at the start of each path, mirroring the shell filter this
# replaced. Kept as one alternation so the positive-list reads as a single source of truth.
_RELEVANT = re.compile(
    r"^(?:"
    r"bajutsu/(?:runner|scenario|drivers|orchestrator)/"
    r"|bajutsu/[^/]+\.py$"
    r"|bajutsu/cli/__init__\.py$"
    r"|bajutsu/cli/_shared\.py$"
    r"|bajutsu/cli/commands/__init__\.py$"
    r"|bajutsu/cli/commands/(?:run|codegen|record)\.py$"
    r"|BajutsuKit/"
    r"|demos/showcase/ios/swiftui/"
    r"|demos/showcase/ios/uikit/"
    r"|demos/showcase/showcase\.config\.yaml$"
    r"|demos/showcase/scenarios/"
    r"|pyproject\.toml$"
    r"|uv\.lock$"
    r"|Makefile$"
    r"|\.github/workflows/e2e\.yml$"
    r"|\.github/actions/bajutsu-e2e/"
    r"|\.github/actions/boot-simulator/"
    r")"
)


def is_relevant(paths: Iterable[str]) -> bool:
    """Whether any changed path is one the on-device E2E jobs actually exercise."""
    return any(_RELEVANT.match(p) for p in paths)


def changed_files(base: str, head: str) -> list[str]:
    """The PR's own changed files, via a merge-base (three-dot) diff of ``base`` and ``head``."""
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _emit(relevant: bool) -> None:
    """Print the verdict and append it to ``GITHUB_OUTPUT`` when the workflow provides one."""
    line = f"relevant={'true' if relevant else 'false'}"
    print(line)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def main() -> int:
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "")
    if not base:
        # workflow_dispatch: no PR context, so nothing to path-gate against — always run.
        _emit(True)
        return 0

    changed = changed_files(base, head)
    print("Changed files:")
    for path in changed:
        print(f"  {path}")
    _emit(is_relevant(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

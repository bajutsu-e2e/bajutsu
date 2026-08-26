#!/usr/bin/env python3
"""Run APM's deploy-tree audit over this repository's own content, and nothing else.

``apm audit --ci`` is the drift gate BE-0390 added: it replays the install and compares the
deployed ``.claude/skills/`` tree against ``apm.lock.yaml``, so a hand-edited deployed file and a
forgotten ``make skills`` both fail. Its content-integrity scan, though, walks each governed deploy
prefix whole, and the ``claude`` target's prefix resolves to ``.claude/`` — the directory Claude
Code also fills with one full checkout per concurrent session, at ``.claude/worktrees/``, the
location CLAUDE.md itself prescribes for isolating sessions. Every such checkout carries its own
``.venv/`` and ``node_modules/``, and third-party sources legitimately contain bidi characters
(jinja2's identifier table, zod's string tests), so the gate went red over files that are neither
this repository's nor present in CI's checkout: red locally, green on the same commit in CI, and
the more parallel sessions the surer the failure (issue #1775).

APM offers no exclude flag, and the prefix comes from its own target registry rather than
``apm.yml``, so the scope has to be narrowed on this side. This mirrors the paths APM reads into a
scratch directory — taking only the files git itself sees, tracked plus untracked ones no ignore
rule covers — and audits that. ``.claude/worktrees/`` is gitignored, so the mirror holds what a
fresh clone holds plus the work in hand, which is what "mirrors CI exactly" claims.

Both of the audit's signals survive the narrowing, because the repository's own files are all
git-visible and are copied verbatim: drift in a deployed skill still fails, and a hidden-Unicode
character in one still fails. Nothing is skipped either — the audit runs on every invocation,
which is the property BE-0390 wanted when it gave this step no skip branch.

Run it with ``make lint-skills`` (in ``make check``). Pure and offline: one ``git ls-files`` read, a
file copy, and APM's own offline audit — no network and no large language model (LLM).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

# What APM reads to resolve its targets, replay the install, and find the deploy trees: the manifest
# pair, the skill sources, and the roots of the governed prefixes (`.agents/skills/`, `.claude/`,
# `.github/` for the `claude` target apm.yml pins). Whole roots rather than the exact prefixes, so
# a registry that adds a prefix under a root already listed here needs no edit.
GOVERNED_PATHS: tuple[str, ...] = (
    "apm.yml",
    "apm.lock.yaml",
    ".apm",
    ".agents",
    ".claude",
    ".github",
)

# --no-policy for the reason the Makefile documents: org-policy discovery would reach
# api.github.com, which only warns when it fails but leaves this offline audit needing the network.
AUDIT_ARGS: tuple[str, ...] = ("audit", "--ci", "--no-policy")

# Both have to reach the mirror for the audit to mean anything; see the guard in `main`.
MANIFESTS: tuple[str, ...] = ("apm.yml", "apm.lock.yaml")


def git_visible_files(root: Path, paths: Sequence[str] = GOVERNED_PATHS) -> list[str] | None:
    """List the paths git sees under *paths*, or None when *root* is not a git checkout.

    Tracked files plus untracked ones no ignore rule covers, so a skill edited but not yet
    committed is still audited while `.claude/worktrees/` — and every `.venv` and `node_modules`
    inside it — is not.

    Args:
        root: Project root to enumerate from; returned paths are relative to it.
        paths: Pathspecs to limit the enumeration to.

    Returns:
        Sorted repository-relative paths, or None when git is absent or *root* is not a git
        checkout.

    Raises:
        subprocess.CalledProcessError: git ran and failed for some other reason — a `safe.directory`
            ownership refusal, a corrupt index. Such a failure says nothing about the scope, so the
            caller must not read it as "no checkout here" and fall back to the broad audit.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *paths],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
            # git translates its own messages, and the classification below reads one of them.
            env={**os.environ, "LC_ALL": "C"},
        )
    except OSError:
        # No git binary at all: a source export, where nothing is enumerable and nothing is wrong.
        return None
    except subprocess.CalledProcessError as error:
        if error.returncode == 128 and "not a git repository" in (error.stderr or "").lower():
            return None
        raise
    return sorted({entry for entry in completed.stdout.split("\0") if entry})


def build_mirror(root: Path, files: Iterable[str], dest: Path) -> int:
    """Copy each git-visible file under *root* into *dest*, keeping its relative path.

    Args:
        root: Project root the paths are relative to.
        files: Repository-relative paths to copy.
        dest: Scratch directory to build the mirror in.

    Returns:
        How many files the mirror holds.
    """
    copied = 0
    for rel in files:
        source = root / rel
        # `--cached` still lists a tracked file deleted from the working tree, and APM's scan never
        # follows a symlink. Leaving both out reproduces what an in-place audit would see.
        if source.is_symlink() or not source.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def main(argv: list[str]) -> int:
    """Mirror the audited paths into a scratch tree and run APM's audit there."""
    parser = argparse.ArgumentParser(description="Audit the deployed agent skills (BE-0390).")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="project root to audit (default: the working directory)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    apm = shutil.which("apm")
    if apm is None:
        print("lint-skills: apm is not on PATH — run `uv sync --group dev`", file=sys.stderr)
        return 1

    try:
        files = git_visible_files(root)
    except subprocess.CalledProcessError as error:
        # git is here and broke: only its own stderr explains why, and the in-place audit would
        # scan the very worktrees this script exists to keep out. Report and stop.
        print("lint-skills: git could not enumerate the audited paths", file=sys.stderr)
        sys.stderr.write(error.stderr or "")
        return error.returncode or 1

    if files is None:
        # No git checkout carries `.claude/worktrees/` — a source export has none — so auditing the
        # tree in place is both correct there and the only thing left to do. Never a skip.
        print("lint-skills: not a git checkout — auditing the project tree in place")
        return subprocess.run([apm, *AUDIT_ARGS], cwd=root).returncode

    with tempfile.TemporaryDirectory(prefix="bajutsu-apm-audit-") as scratch:
        mirror = Path(scratch)
        print(f"lint-skills: auditing {build_mirror(root, files, mirror)} git-visible file(s)")
        for manifest in MANIFESTS:
            if (mirror / manifest).is_file():
                continue
            # Either absence exits 0 having audited nothing: without `apm.yml` APM reports nothing
            # to check, and without the lockfile it declares no dependencies and runs one check of
            # ten, neither of them content-integrity or drift. That green is the one outcome this
            # gate must never produce.
            print(
                f"lint-skills: {manifest} did not reach the audit mirror — refusing a vacuous audit",
                file=sys.stderr,
            )
            return 1
        return subprocess.run([apm, *AUDIT_ARGS], cwd=mirror).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

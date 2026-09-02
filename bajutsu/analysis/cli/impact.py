"""`bajutsu impact` — the scenario steps a source change is likely to affect (no device, no AI, BE-0321).

Read-only and advisory, of a piece with `coverage`: it inverts the suite's static scenario analysis
into a map from each stable id / screen / endpoint to the `(scenario, step)` pairs that reference it,
reads a change as a `git` diff, and reports the steps whose referenced literals the diff touched.
The match is a plain string search over ids the suite already declares, so it stays app-agnostic —
no per-language parsing, no model. It never runs a scenario and never gates CI: it exits 0 whatever
the affected set (over-selection is the safe direction), and only a missing config / scenarios dir,
an unreadable scenario, or a `git` / diff read failure exits 2. When the diff carries a change that
maps to no referenced literal, the report flags itself incomplete so a CI narrowing falls back to the
full suite (the conservative fallback) — the verdict always stays with the deterministic runner.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import typer

from bajutsu.analysis import impact as _impact
from bajutsu.cli._shared import DEFAULT_CONFIG, _load_effective
from bajutsu.common.scenario import load_scenarios_dir

# Git repository-location variables that would override `-C <repo>` and point git at the wrong tree.
# They leak in when `bajutsu impact` is invoked from inside another git operation (a hook, a wrapper),
# so the git calls below strip them, making `--repo` / the current directory the authoritative repo.
_GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


def _git_env() -> dict[str, str]:
    """The process environment with git's repo-location variables removed, so `-C <repo>` wins."""
    return {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_VARS}


def impact(
    target_name: str = typer.Option(..., "--target"),
    config: str = typer.Option(DEFAULT_CONFIG),
    range_: str = typer.Option(
        "HEAD",
        "--range",
        help="a git revision range to diff (e.g. HEAD~1..HEAD); the default HEAD diffs the working "
        "tree against HEAD. Ignored when --diff is given.",
    ),
    diff: str = typer.Option(
        "",
        "--diff",
        help="read a unified diff from this path (or '-' for stdin) instead of running git",
    ),
    repo: str = typer.Option(
        "", "--repo", help="the git repository to diff in (default: the current directory)"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the report as JSON instead of text"),
) -> None:
    """Report which scenario steps a source change is likely to affect (read-only, advisory).

    Builds a reverse index from the target's suite (stable id / screen / endpoint → step), reads a
    change as a `git` diff — from `--range` (default: the working tree against `HEAD`, untracked files
    included) or a diff supplied via `--diff` — and reports the affected `(scenario, step)` pairs, each
    carrying the touched literal that implicates it, as text or `--json`. When the change contains a
    part that maps to no referenced literal, the report is flagged incomplete (a full run is
    warranted). Read-only:
    it never runs a scenario and never gates CI — it exits 0 whatever the affected set; only a missing
    config / scenarios dir, an unreadable scenario, or a `git` / diff read failure exits 2.
    """
    eff = _load_effective(config, target_name)
    if eff.evidence_dirs.scenarios is None:
        typer.echo(
            f"target '{target_name}' has no scenarios dir (set targets.{target_name}.scenarios)"
        )
        raise typer.Exit(2)
    scenarios_dir = Path(eff.evidence_dirs.scenarios)
    if not scenarios_dir.is_dir():
        typer.echo(f"scenarios dir not found: {eff.evidence_dirs.scenarios}")
        raise typer.Exit(2)
    try:
        scenarios = load_scenarios_dir(scenarios_dir)
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenarios: {e}")
        raise typer.Exit(2) from None

    changed = _impact.parse_diff(_read_diff(diff)) if diff else _git_changed(range_, repo)
    report = _impact.impact(_impact.reverse_index(scenarios), changed)

    if as_json:
        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
    else:
        typer.echo(_impact.render(report))


def _read_diff(source: str) -> str:
    """A unified diff from a file (or stdin when `source` is '-'). Exits 2 on an unreadable file.

    A file that is not valid UTF-8 raises `UnicodeDecodeError` (a `ValueError`), caught here alongside
    `OSError` so an unreadable diff exits 2 with a message, not an uncaught traceback.
    """
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        typer.echo(f"failed to read diff: {e}")
        raise typer.Exit(2) from None


def _git_changed(range_: str, repo: str) -> list[_impact.ChangedFile]:
    """The files `git diff <range>` reports in `repo`, plus untracked files in working-tree mode.

    `git diff` compares tracked content only, so an untracked new source file — a common case when
    adding a screen — is invisible to it. In working-tree mode (a range with no `..`), the untracked,
    non-ignored files are folded in so the report accounts for them rather than falsely claiming
    completeness: a readable one contributes its lines to the match, an unreadable (binary) one is
    marked unattributable. A commit-to-commit range (`A..B`) has no working tree, so none are added.
    """
    changed = _impact.parse_diff(_git_diff(range_, repo))
    if ".." not in range_:
        changed += _untracked_changed(repo)
    return changed


def _git_diff(range_: str, repo: str) -> str:
    """The unified diff `git diff <range>` produces in `repo`. Exits 2 when git is unavailable or fails.

    Decodes with `errors="replace"` so a diff carrying bytes invalid for the locale still parses (a
    stray undecodable byte becomes U+FFFD rather than crashing the read) — the string match only needs
    the added/removed line text.
    """
    try:
        out = subprocess.run(
            ["git", "-C", repo or ".", "diff", range_],  # noqa: S607 — git resolved on PATH, argv list
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        typer.echo(f"failed to run git: {e}")
        raise typer.Exit(2) from None
    if out.returncode != 0:
        typer.echo(f"git diff failed: {out.stderr.strip() or f'exit {out.returncode}'}")
        raise typer.Exit(2)
    return out.stdout


def _untracked_changed(repo: str) -> list[_impact.ChangedFile]:
    """The repo's untracked, non-`.gitignore`d files as `ChangedFile`s (their whole content is new).

    `git ls-files --others --exclude-standard` lists exactly the files a plain `git diff` misses; each
    is read as text so its referenced ids match, or — when it can't be decoded — marked binary so it
    is unattributable. A `git` failure here is non-fatal: the tracked diff already parsed, and dropping
    untracked detection at worst under-reports, so it degrades to the documented tracked-only behavior
    rather than exiting.
    """
    try:
        out = subprocess.run(
            ["git", "-C", repo or ".", "ls-files", "--others", "--exclude-standard", "-z"],  # noqa: S607 — git on PATH, argv list
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    root = Path(repo or ".")
    changed: list[_impact.ChangedFile] = []
    for rel in (p for p in out.stdout.split("\0") if p):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (
            OSError,
            ValueError,
        ):  # unreadable or binary — can't be string-matched, so unattributable
            changed.append(_impact.ChangedFile(path=rel, lines=[], binary=True))
        else:
            changed.append(_impact.ChangedFile(path=rel, lines=text.splitlines()))
    return changed


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(impact)

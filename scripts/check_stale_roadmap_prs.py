#!/usr/bin/env python3
"""Re-check open roadmap PRs against the current template, and open a fix PR on drift (BE-0149).

Item 1 (the placeholder-aware format check) only fires when a PR's branch is pushed to or merged;
a PR that receives no further pushes never re-runs it, so a template change (a new required field, a
new section) can land on ``main`` while an old PR's placeholder silently drifts out of shape — the
exact way BE-0137/BE-0138 slipped through. This closes that latency gap: it runs whenever a
template-affecting commit lands on ``main`` (the ``roadmap-drift-check`` workflow), lists every open,
same-repo PR that touches ``roadmaps/**`` (a fork branch can't be pushed to or targeted by a
same-repo PR, the same constraint ``roadmap-promote.yml`` already applies), and for each:

1. Fetches the PR's own ``roadmaps/**`` file content via the GitHub Contents API (read-only — the PR
   branch itself is never touched, so it cannot dismiss a stale approval the way a pushed commit
   would, BE-0089) and overlays it onto a throwaway copy of the current ``roadmaps/`` tree, simulating
   "this PR's files, checked against today's template" without a real git merge.
2. Runs the shared, placeholder-aware format check (``check_roadmap_format``) against that overlay.
3. On drift, tries the mechanical fixer (``fix_roadmap_drift``) — the same narrow shape PR #568 fixed
   by hand: drop a banned metadata field, insert a missing section with the template skeleton. If it
   actually changes anything, pushes a new ``roadmap-fix/pr-<n>`` branch based on the PR's own head,
   opens a small PR whose base is that branch, and posts/updates one marker comment on the original
   PR linking to it. The fix only lands when the PR's author merges it — never an automated push to
   their branch.

Scope: this checks per-item **format** shape only (headings, metadata fields) — not index-table
staleness, which ``roadmap-promote`` already reconciles per-PR independently of any template change.

Known limitation: the overlay always checks against **this checkout's own** ``check_roadmap_format``
(main's, imported normally at module load), never a PR's own version of it. A PR that touches
``scripts/check_roadmap_format.py`` or ``scripts/roadmap_ids.py`` itself is checked against main's
rules, not its own — a real gap versus a true merge, but deliberate: this job's overlay already
ingests PR-supplied *text* into files (Contents-API fetches, never a git merge or checkout of the
PR's branch), which is a bounded surface — the fixer only ever rewrites metadata rows and headings
via fixed regexes. Dynamically importing and running a PR's own copy of a ``.py`` file would instead
*execute* PR-supplied bytes with this job's write-capable App token — arbitrary code execution, a
strictly larger blast radius than parsing markdown with our own fixed logic, and exactly the kind of
surface ``roadmap-id.yml``'s bypass identity is kept minimal to avoid (BE-0089). A roadmap PR that
also edits the checker is rare enough that item 1 (which runs the PR's *own* checker on every push to
it) is an adequate backstop for that case.

Usage::

    python scripts/check_stale_roadmap_prs.py            # fix drifted PRs, open/update fix PRs
    python scripts/check_stale_roadmap_prs.py --check     # report drift only, mutate nothing

It calls the network (``gh``, ``git push``), so it never runs inside ``make check``.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import copytree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_cli
from check_roadmap_format import format_problems, unresolved_be_xxxx_references
from fix_roadmap_drift import fix_unknown_fields_and_missing_sections

ROADMAP = Path("roadmaps")
REPO = "bajutsu-e2e/bajutsu"
FIX_BRANCH_PREFIX = "roadmap-fix/pr-"
MARKER = "<!-- roadmap-drift-fix-pr -->"


@dataclass(frozen=True)
class RoadmapPr:
    """An open, same-repo PR that touches ``roadmaps/**``."""

    number: int
    head_ref: str  # the PR's own branch name — the fix PR's base
    head_sha: str  # pinned so content fetches are immune to the branch moving mid-run
    changed_paths: tuple[str, ...]  # every changed path under roadmaps/


def list_open_roadmap_prs() -> list[RoadmapPr]:
    """Every open, same-repo PR touching ``roadmaps/**``, excluding this workflow's own fix PRs."""
    raw = gh_cli.run(
        [
            "pr",
            "list",
            "--repo",
            REPO,
            "--state",
            "open",
            "--json",
            "number,headRefName,headRefOid,isCrossRepository",
            "--limit",
            "200",
        ],
        capture=True,
    )
    prs: list[RoadmapPr] = []
    for entry in json.loads(raw):
        # A fork branch can't be pushed to or targeted by a same-repo PR (roadmap-promote.yml
        # applies the same constraint); a fix PR from a prior run would otherwise re-enter the scan.
        if entry["isCrossRepository"] or entry["headRefName"].startswith(FIX_BRANCH_PREFIX):
            continue
        files = json.loads(
            gh_cli.run(
                ["api", f"repos/{REPO}/pulls/{entry['number']}/files", "--paginate"], capture=True
            )
        )
        changed = tuple(f["filename"] for f in files if f["filename"].startswith("roadmaps/"))
        if changed:
            prs.append(
                RoadmapPr(entry["number"], entry["headRefName"], entry["headRefOid"], changed)
            )
    return prs


def fetch_file_content(head_sha: str, path: str) -> str | None:
    """The file's content at this commit via the Contents API, or ``None`` if it doesn't exist
    there (deleted, or renamed away) — read-only, touches nothing on the PR's branch.

    A transient API failure (rate limit, a 5xx) also reads as ``None`` here, indistinguishable from
    a real deletion — it surfaces downstream as spurious "drift"/"needs a human" rather than a
    crash, and no fix is ever computed from missing content (``compute_fixes`` skips ``None``), so
    the failure mode is a noisy false positive, never a wrong write. Acceptable for a job this
    infrequent; not worth a retry/backoff for it.
    """
    result = gh_cli.run_allow_failure(
        ["api", f"repos/{REPO}/contents/{path}?ref={head_sha}", "-q", ".content"]
    )
    if result.returncode != 0:
        return None
    return base64.b64decode(result.stdout).decode("utf-8")


def build_overlay(main_roadmap: Path, dest: Path, file_contents: dict[str, str | None]) -> None:
    """Copy ``main_roadmap`` into ``dest``, then apply the PR's own content for each changed path
    (``None`` deletes it) — a read-only simulation of "this PR's files against today's template"."""
    copytree(main_roadmap, dest)
    for rel_path, content in file_contents.items():
        target = dest / Path(rel_path).relative_to(ROADMAP)
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def detect_drift(main_roadmap: Path, file_contents: dict[str, str | None]) -> list[str]:
    """Format problems the overlay tree has that the real ``main`` tree does not."""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "roadmaps"
        build_overlay(main_roadmap, dest, file_contents)
        return format_problems(dest) + unresolved_be_xxxx_references(dest)


def compute_fixes(file_contents: dict[str, str | None]) -> dict[str, str]:
    """Mechanically fixed content for each changed ``.md`` file that needs it.

    Empty when no changed file has a fixable shape — the caller then knows the drift needs a human,
    not just an unlucky diff.
    """
    fixes: dict[str, str] = {}
    for path, content in file_contents.items():
        if content is None or not path.endswith(".md"):
            continue
        lang = "ja" if path.endswith("-ja.md") else "en"
        fixed = fix_unknown_fields_and_missing_sections(content, lang=lang)
        if fixed != content:
            fixes[path] = fixed
    return fixes


def open_or_update_fix_pr(pr: RoadmapPr, fixes: dict[str, str]) -> str:
    """Push a ``roadmap-fix/pr-<n>`` branch off the PR's own head carrying ``fixes``, and
    open (or, on a re-run, silently update) a PR whose base is the original PR's branch. Returns
    the fix branch name, for the caller to point the marker comment at."""
    fix_branch = f"{FIX_BRANCH_PREFIX}{pr.number}"
    # Base the fix branch on pr.head_sha — the exact commit fixes was computed from — not on
    # pr.head_ref's current tip. The branch can move between listing the PR and reaching here (e.g.
    # the author pushes a new commit); basing on the live ref would force-push the fix on top of
    # that newer commit, silently clobbering it with content computed from a stale snapshot.
    subprocess.run(["git", "fetch", "origin", pr.head_sha], check=True)
    subprocess.run(["git", "checkout", "-B", fix_branch, pr.head_sha], check=True)
    for path, content in fixes.items():
        Path(path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", *fixes.keys()], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"docs(roadmap): fix template drift on #{pr.number}"], check=True
    )
    subprocess.run(["git", "push", "--force", "origin", f"{fix_branch}:{fix_branch}"], check=True)

    existing = json.loads(
        gh_cli.run(
            [
                "pr",
                "list",
                "--repo",
                REPO,
                "--head",
                fix_branch,
                "--state",
                "open",
                "--json",
                "number",
            ],
            capture=True,
        )
    )
    if not existing:
        gh_cli.run(
            [
                "pr",
                "create",
                "--repo",
                REPO,
                "--base",
                pr.head_ref,
                "--head",
                fix_branch,
                "--title",
                f"docs(roadmap): fix template drift on #{pr.number}",
                "--body",
                (
                    f"Mechanically fixes the roadmap-template drift BE-0149's periodic re-check "
                    f"found on #{pr.number}: a banned metadata field and/or a missing required "
                    f"section, restored to the current template shape. Merge this into "
                    f"`{pr.head_ref}` to pick up the fix.\n\n"
                    f"🤖 Generated by the `roadmap-drift-check` workflow (BE-0149)."
                ),
            ]
        )
        print(f"Opened a fix PR for #{pr.number} against `{pr.head_ref}`.")
    else:
        print(f"Updated the existing fix PR for #{pr.number} (`{fix_branch}`).")
    return fix_branch


def post_marker_comment(pr: RoadmapPr, fix_branch: str) -> None:
    """Post the single drift-notice comment on the original PR, or update it if already present."""
    body = (
        f"{MARKER}\n"
        f"This PR's roadmap item drifted out of the current template shape while it sat in review "
        f"(BE-0149). A mechanical fix is ready on `{fix_branch}` — merge its PR into this branch to "
        f"pick it up."
    )
    comments = json.loads(
        gh_cli.run(["api", f"repos/{REPO}/issues/{pr.number}/comments", "--paginate"], capture=True)
    )
    existing = next((c for c in comments if c["body"].startswith(MARKER)), None)
    if existing:
        gh_cli.run(
            [
                "api",
                "--method",
                "PATCH",
                f"repos/{REPO}/issues/comments/{existing['id']}",
                "-f",
                f"body={body}",
            ]
        )
    else:
        gh_cli.run(["pr", "comment", str(pr.number), "--repo", REPO, "--body", body])


def process_pr(pr: RoadmapPr, main_roadmap: Path, *, dry_run: bool) -> bool:
    """Check one PR; return whether it has drift. Opens/updates a fix PR unless ``dry_run``."""
    file_contents = {path: fetch_file_content(pr.head_sha, path) for path in pr.changed_paths}
    problems = detect_drift(main_roadmap, file_contents)
    if not problems:
        return False

    print(f"#{pr.number}: template drift detected:")
    for problem in problems:
        print(f"  {problem}")
    if dry_run:
        return True

    fixes = compute_fixes(file_contents)
    if not fixes:
        print(f"#{pr.number}: no mechanical fix applies; a human must fix this one.")
        return True

    fix_branch = open_or_update_fix_pr(pr, fixes)
    post_marker_comment(pr, fix_branch)
    return True


def main(argv: list[str]) -> int:
    dry_run = "--check" in argv
    prs = list_open_roadmap_prs()
    if not prs:
        print("No open, same-repo PRs touch roadmaps/**; nothing to check.")
        return 0

    any_drift = False
    for pr in prs:
        # A fix leaves the checkout on that PR's fix branch (open_or_update_fix_pr checks it out to
        # write and commit the fix) — the next iteration's own `git fetch` + `checkout -B ...`
        # resets it before touching anything, so this is safe, but by design rather than by accident.
        if process_pr(pr, ROADMAP, dry_run=dry_run):
            any_drift = True

    if not any_drift:
        print(
            f"Checked {len(prs)} open roadmap PR(s); none have drifted from the current template."
        )
    return 1 if (dry_run and any_drift) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

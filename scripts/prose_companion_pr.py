#!/usr/bin/env python3
"""Ship the advisory reviewer's wording-only findings as a companion PR (BE-0343).

A wording finding from ``claude-review`` (BE-0203) is never a correctness bug, yet fixing it on the
pull request that raised it costs that pull request a full CI cycle. This turns each such finding
into a small companion pull request instead: it reads the findings already posted inline, applies
each one's own ``suggestion`` block mechanically, and opens the result against the **source pull
request's own branch** — so the fix is reviewable while that pull request is still open, and its
branch and CI stay untouched until a human merges the companion.

**No LLM runs here.** BE-0203 already drafted the fix, as a ``suggestion`` block, at review time;
this only decides whether that exact text still applies. It does so by reconstructing what the
finding's line looked like when it was posted (from the comment's own ``diff_hunk``) and comparing
that against the file at the pull request's current head: a match is spliced in, a mismatch — the
source pull request edited that line since — is skipped and reported, never guess-applied.

The companion branch is rebuilt **fresh from the current head every run** rather than patched
forward, so a rebase on the source branch (routine under `CLAUDE.md`'s "rebase, don't drift") has
nothing to re-sync: nothing carries over. Every currently posted finding is reapplied each run, not
only the new ones. The rolling branch is clobber-guarded exactly as BE-0222's refreshers guard
theirs — ``is_bot_authored`` is imported from there rather than restated, so the two cannot drift.

Like ``refresh_pr.py``, the git/``gh`` orchestration calls the network and never runs inside
``make check``; the tests cover the pure parts (hunk parsing, suggestion extraction, the finding
filter, the apply/skip decision, the body text).

Usage::

    python scripts/prose_companion_pr.py --pr 123 --source-branch claude/be-0001-x \\
        --repo-dir companion --bot-email "app-slug[bot]@users.noreply.github.com"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_cli
from refresh_pr import is_bot_authored

REPO = "bajutsu-e2e/bajutsu"

# The two decorations a finding must carry to be applied here: the reviewer's identity prefix
# (claude-review-prompt.md's "Identify yourself as Claude Code" rule) and the prose-lens marker it
# adds to a wording finding — and only to one. Requiring both is what keeps a design, security, or
# correctness `suggestion` on code out of a mechanically-applied companion PR.
CLAUDE_PREFIX = "🤖 **Claude Code**"
PROSE_MARKER = "(non-blocking, prose)"

_SUGGESTION_RE = re.compile(r"^```suggestion[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
_HUNK_START_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


@dataclass(frozen=True)
class Finding:
    """One wording finding, resolved to a splice: where it lands, what it expects, what it writes."""

    comment_id: int
    path: str
    start_line: int
    end_line: int
    expected: tuple[str, ...]
    replacement: tuple[str, ...]


def parse_diff_hunk(hunk: str) -> dict[int, str]:
    """The hunk's post-image, as new-file line number to text.

    This is what the finding's lines read *when the finding was posted*, which is the only record of
    that available later — GitHub keeps ``diff_hunk`` as of comment creation while it moves ``line``
    forward. Comparing it against the head file is the whole drift check.
    """
    lines: dict[int, str] = {}
    number = 0
    for raw in hunk.splitlines():
        header = _HUNK_START_RE.match(raw)
        if header:
            number = int(header.group(1)) - 1
            continue
        if raw.startswith(("-", "\\")):  # removed line, or "\ No newline at end of file"
            continue
        number += 1
        lines[number] = raw[1:]
    return lines


def extract_suggestion(body: str) -> tuple[str, ...] | None:
    """The replacement lines of the comment's ``suggestion`` block, or ``None`` when it carries none.

    An empty block is a deletion, so it returns an empty tuple — distinct from ``None``, which means
    the finding proposed no mechanical fix at all and cannot be applied.
    """
    match = _SUGGESTION_RE.search(body)
    if not match:
        return None
    content = match.group(1)
    if content.endswith("\n"):
        content = content[:-1]
    return tuple(content.split("\n")) if content else ()


def findings_from_comments(comments: list[dict[str, Any]]) -> list[Finding]:
    """The applicable wording findings among a pull request's inline comments.

    Everything that cannot be applied mechanically drops out here rather than later: a reply, a
    finding from another lens, one with no ``suggestion`` block, one on the pre-image side, one gone
    outdated (``line`` is null), and one whose hunk no longer covers its own span.
    """
    findings: list[Finding] = []
    for comment in comments:
        body = str(comment.get("body") or "").replace("\r\n", "\n")
        if not body.startswith(CLAUDE_PREFIX) or PROSE_MARKER not in body:
            continue
        if comment.get("in_reply_to_id") is not None:
            continue
        if comment.get("side") not in (None, "RIGHT"):
            continue
        line, original_line = comment.get("line"), comment.get("original_line")
        if not isinstance(line, int) or not isinstance(original_line, int):
            continue
        replacement = extract_suggestion(body)
        if replacement is None:
            continue
        start_line = comment.get("start_line") or line
        original_start = comment.get("original_start_line") or original_line
        post_image = parse_diff_hunk(str(comment.get("diff_hunk") or ""))
        expected = [post_image.get(n) for n in range(original_start, original_line + 1)]
        if any(text is None for text in expected):
            continue
        findings.append(
            Finding(
                comment_id=int(comment["id"]),
                path=str(comment["path"]),
                start_line=int(start_line),
                end_line=line,
                expected=tuple(text for text in expected if text is not None),
                replacement=replacement,
            )
        )
    return findings


def apply_to_lines(
    lines: list[str], findings: list[Finding]
) -> tuple[list[str], list[Finding], list[Finding]]:
    """Splice every finding whose span still matches, and report the ones that no longer do.

    Applied bottom-up so an earlier splice cannot shift a later finding's line numbers; a finding
    that overlaps one already applied therefore fails its own match and is skipped rather than
    corrupting the file.
    """
    result = list(lines)
    applied: list[Finding] = []
    skipped: list[Finding] = []
    for finding in sorted(findings, key=lambda f: f.start_line, reverse=True):
        start, end = finding.start_line - 1, finding.end_line
        if start < 0 or end > len(result) or tuple(result[start:end]) != finding.expected:
            skipped.append(finding)
            continue
        result[start:end] = list(finding.replacement)
        applied.append(finding)
    return result, applied, skipped


def pr_body(source_pr: int, applied: list[Finding], skipped: list[Finding]) -> str:
    """The companion PR body: what it carries, why it exists, and what it declined to apply."""
    applied_lines = "".join(
        f"- `{f.path}`:{f.start_line} (comment {f.comment_id})\n" for f in applied
    )
    skipped_note = ""
    if skipped:
        listed = "".join(f"- `{f.path}`:{f.start_line} (comment {f.comment_id})\n" for f in skipped)
        skipped_note = (
            f"\nNot applied — the source pull request has edited these lines since the finding was "
            f"posted, so the suggestion no longer matches and was **skipped rather than "
            f"guess-applied**:\n\n{listed}"
        )
    return (
        f"Wording-only fixes for #{source_pr} (BE-0343).\n\n"
        f"The advisory reviewer (BE-0203) posted these as `{PROSE_MARKER}` findings on #{source_pr}. "
        f"Applying them there would re-run that pull request's full CI for a change with no "
        f"behavioral risk, so they land here instead — based on that pull request's own branch, so "
        f"merging this ships the fix while #{source_pr} is still open.\n\n"
        f"**No LLM ran to produce this.** Each change is the finding's own `suggestion` block, "
        f"applied only where the file still matches what the finding saw.\n\n"
        f"Applied:\n\n{applied_lines}{skipped_note}\n"
        f"This branch is rebuilt from #{source_pr}'s head on every run, so a rebase over there needs "
        f"no action here. Push your own fixups onto it and the next run will *skip* rather than "
        f"overwrite them.\n\n"
        f"🤖 Generated by the `prose-companion` job (BE-0343)."
    )


def reply_body(companion_pr: int) -> str:
    """The reply left on the source finding's thread before it is resolved."""
    return (
        f"🤖 Applied mechanically in #{companion_pr} (BE-0343) — this finding's own `suggestion` "
        f"block, on a companion branch based on this pull request's head, so this pull request's CI "
        f"is not re-run for a wording fix. Merge #{companion_pr} to land it here."
    )


def _git(repo_dir: str, args: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", "-C", repo_dir, *args], text=True, capture_output=capture, check=True
    )
    return result.stdout if capture else ""


def _apply_findings(repo_dir: str, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Rewrite each touched file in the working tree; report what applied and what drifted."""
    applied: list[Finding] = []
    skipped: list[Finding] = []
    by_path: dict[str, list[Finding]] = {}
    for finding in findings:
        by_path.setdefault(finding.path, []).append(finding)

    for path, group in by_path.items():
        target = Path(repo_dir) / path
        if not target.is_file():
            skipped.extend(group)
            continue
        original = target.read_text(encoding="utf-8")
        rewritten, path_applied, path_skipped = apply_to_lines(original.split("\n"), group)
        applied.extend(path_applied)
        skipped.extend(path_skipped)
        if path_applied:
            target.write_text("\n".join(rewritten), encoding="utf-8")
    return applied, skipped


def _remote_tip(repo_dir: str, branch: str) -> tuple[str, str] | None:
    """The companion branch's remote tip as (SHA, committer email), or ``None`` if it doesn't exist."""
    if not _git(repo_dir, ["ls-remote", "--heads", "origin", branch], capture=True).strip():
        return None
    _git(repo_dir, ["fetch", "origin", branch])
    sha = _git(repo_dir, ["rev-parse", "FETCH_HEAD"], capture=True).strip()
    committer = _git(repo_dir, ["log", "-1", "--format=%ce", "FETCH_HEAD"], capture=True).strip()
    return sha, committer


def _open_or_reuse_pr(*, branch: str, base: str, title: str, body: str) -> int:
    """Reuse the open companion PR for this branch, or open one; return its number.

    Opened ready for review, not Draft: it carries only wording changes a human is meant to read and
    merge quickly, and a Draft would add a marking step to that path for no gain.
    """
    existing = json.loads(
        gh_cli.run(
            ["pr", "list", "--repo", REPO, "--head", branch, "--state", "open", "--json", "number"],
            capture=True,
        )
    )
    if existing:
        gh_cli.run(["pr", "edit", branch, "--repo", REPO, "--body", body])
        number = int(existing[0]["number"])
        print(f"Updated the existing companion PR #{number} for `{branch}`.")
        return number
    gh_cli.run(
        [
            "pr",
            "create",
            "--repo",
            REPO,
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
    )
    created = json.loads(
        gh_cli.run(
            ["pr", "list", "--repo", REPO, "--head", branch, "--state", "open", "--json", "number"],
            capture=True,
        )
    )
    number = int(created[0]["number"])
    print(f"Opened companion PR #{number} for `{branch}` against `{base}`.")
    return number


_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes { id isResolved comments(first: 1) { nodes { databaseId } } }
      }
    }
  }
}
"""


def _unresolved_threads(pr: int) -> dict[int, str]:
    """Map each unresolved thread's first comment id to its GraphQL thread id.

    Only unresolved threads are returned, which is what stops a rerun from replying again to a
    thread this job already answered and resolved on an earlier push.
    """
    owner, name = REPO.split("/", 1)
    raw = gh_cli.run(
        [
            "api",
            "graphql",
            "-f",
            f"query={_THREADS_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr}",
        ],
        capture=True,
    )
    nodes = json.loads(raw)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    threads: dict[int, str] = {}
    for node in nodes:
        if node["isResolved"]:
            continue
        comments = node["comments"]["nodes"]
        if comments:
            threads[int(comments[0]["databaseId"])] = str(node["id"])
    return threads


def _answer_threads(*, pr: int, companion_pr: int, applied: list[Finding]) -> None:
    """Reply to each applied finding's thread naming the companion PR, then resolve it."""
    threads = _unresolved_threads(pr)
    body = reply_body(companion_pr)
    for finding in applied:
        thread_id = threads.get(finding.comment_id)
        if thread_id is None:  # already answered on an earlier push
            continue
        gh_cli.run(
            [
                "api",
                f"repos/{REPO}/pulls/{pr}/comments/{finding.comment_id}/replies",
                "-f",
                f"body={body}",
            ],
        )
        gh_cli.run(
            [
                "api",
                "graphql",
                "-f",
                "query=mutation($id: ID!) { resolveReviewThread(input: {threadId: $id})"
                " { thread { id } } }",
                "-F",
                f"id={thread_id}",
            ],
        )


def run(args: argparse.Namespace) -> int:
    """Read the findings, apply them to a fresh companion branch, and ship it."""
    comments: list[dict[str, Any]] = json.loads(
        gh_cli.run(["api", "--paginate", f"repos/{REPO}/pulls/{args.pr}/comments"], capture=True)
    )
    findings = findings_from_comments(comments)
    if not findings:
        print(f"No `{PROSE_MARKER}` findings with a suggestion block on #{args.pr} (no-op).")
        return 0

    applied, skipped = _apply_findings(args.repo_dir, findings)
    for finding in skipped:
        print(
            f"::notice::skipping {finding.path}:{finding.start_line} — #{args.pr} has edited that "
            f"line since the finding was posted, so the suggestion no longer matches."
        )
    if not applied or not _git(args.repo_dir, ["status", "--porcelain"], capture=True).strip():
        print("Every finding is already applied on the source branch, or none matched (no-op).")
        return 0

    branch = f"prose-fix/pr-{args.pr}"
    tip = _remote_tip(args.repo_dir, branch)
    if not is_bot_authored(tip[1] if tip else None, args.bot_email):
        print(
            f"::warning::`{branch}` has a human-committed tip; skipping to avoid clobbering it. "
            f"Merge or delete the branch to let the companion PR resume."
        )
        return 0

    _git(args.repo_dir, ["checkout", "-B", branch])
    _git(args.repo_dir, ["add", "--", *sorted({f.path for f in applied})])
    _git(args.repo_dir, ["commit", "-m", f"docs(prose): apply review wording fixes for #{args.pr}"])
    if tip is None:
        _git(args.repo_dir, ["push", "origin", f"{branch}:{branch}"])
    else:
        # Lease against the tip just inspected, so a push landing in the read->push window fails
        # loudly rather than being overwritten.
        _git(
            args.repo_dir,
            ["push", f"--force-with-lease={branch}:{tip[0]}", "origin", f"{branch}:{branch}"],
        )

    companion_pr = _open_or_reuse_pr(
        branch=branch,
        base=args.source_branch,
        title=f"docs(prose): wording fixes for #{args.pr}",
        body=pr_body(args.pr, applied, skipped),
    )
    _answer_threads(pr=args.pr, companion_pr=companion_pr, applied=applied)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Open/update a prose companion PR (BE-0343).")
    parser.add_argument("--pr", type=int, required=True, help="the source pull request's number")
    parser.add_argument(
        "--source-branch", required=True, help="the source PR's head branch — the companion's base"
    )
    parser.add_argument(
        "--repo-dir", required=True, help="a checkout of the source PR's head to build on"
    )
    parser.add_argument("--bot-email", required=True, help="the automation bot's commit email")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

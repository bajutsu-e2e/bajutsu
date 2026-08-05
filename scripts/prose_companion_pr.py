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

Two gates bound what this can write, because it writes under a privileged App token:

- **Who.** A comment qualifies only when GitHub says the *reviewer account* posted it. The
  ``🤖 **Claude Code**`` prefix and the ``(non-blocking, prose)`` marker are plain text anyone with
  comment access could type, so they classify a finding — they never authenticate one.
- **Where.** A finding outside ``PATH_ALLOWLIST`` is refused however it is marked, the same hard
  path partition that is ``refresh_pr.py``'s whole safety model. A finding the reviewer mismarks
  onto product code therefore cannot land mechanically; it stays an ordinary inline comment for the
  author to weigh.

Nothing is dropped quietly. Every finding this refuses to apply — wrong path, no suggestion block,
drifted line, missing file — is warned about and listed in the companion pull request's body, since
a tool that hides a failure is worse than none (prime directive 2).

The companion branch is rebuilt **fresh from the current head every run** rather than patched
forward, so a rebase on the source branch (routine under `CLAUDE.md`'s "rebase, don't drift") has
nothing to re-sync: nothing carries over. Every currently posted finding is reapplied each run, not
only the new ones. The rolling branch is clobber-guarded exactly as BE-0222's refreshers guard
theirs — ``is_bot_authored`` and ``RemoteTip`` are imported from there rather than restated, so the
two cannot drift.

Like ``refresh_pr.py``, the git/``gh`` orchestration calls the network and never runs inside
``make check``; the tests cover the pure parts (hunk parsing, suggestion extraction, the finding
filter, the apply/skip decision, the body text).

Usage::

    python scripts/prose_companion_pr.py --pr 123 --source-branch claude/be-0001-x \\
        --repo-dir companion --bot-email "app-slug[bot]@users.noreply.github.com"
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_cli
from refresh_pr import RemoteTip, is_bot_authored

REPO = "bajutsu-e2e/bajutsu"

# The account the review posts under. claude-review.yml runs the Claude Code action with the default
# GITHUB_TOKEN, so every finding — auto-review and the on-demand `@claude review` path alike —
# arrives as `github-actions[bot]`. This, not the comment text, is the identity gate.
REVIEWER_LOGIN = "github-actions[bot]"

# How a wording finding is *classified* once its author is established: the reviewer's identity
# prefix (claude-review-prompt.md's "Identify yourself as Claude Code" rule) plus the marker the
# prose lenses — and only the prose lenses — add. Both are copyable text; neither authenticates.
CLAUDE_PREFIX = "🤖 **Claude Code**"
PROSE_MARKER = "(non-blocking, prose)"

# Where a finding may be applied. The two prose lenses judge documentation and roadmap prose, so
# nothing else can be a legitimate target — and confining the writes here means a mismarked finding
# on product code is refused rather than committed. `fnmatch`'s `*` crosses `/`, so each pattern
# covers its whole subtree, the same matcher and reasoning as refresh_pr.py's allowlist. Root-level
# prose (`CLAUDE.md`, `README.md`) is deliberately out: it is contract surface, excluded here for
# the same reason docs-refresh excludes it from its own allowlist.
PATH_ALLOWLIST = ("docs/*.md", "roadmaps/*.md")

# The one refusal reason that is not a problem: it is what every run sees once a companion PR
# has merged and its fix is on the source branch.
APPLIED_ALREADY = "already applied on the source branch"

_SUGGESTION_RE = re.compile(r"^```suggestion[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
_HUNK_START_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
_PR_URL_RE = re.compile(r"/pull/(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    """One wording finding, resolved to a splice: where it lands, what it expects, what it writes."""

    comment_id: int
    path: str
    start_line: int
    end_line: int
    expected: tuple[str, ...]
    replacement: tuple[str, ...]


@dataclass(frozen=True)
class Refused:
    """A finding that could not be applied, and the reason — surfaced, never swallowed."""

    comment_id: int
    path: str
    reason: str


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
    """The replacement lines of the comment's one ``suggestion`` block, or ``None``.

    ``None`` covers both "no block at all" and "more than one block": with several, which one the
    finding meant is a judgment call, and guessing is exactly what this must not do. An empty block
    is unambiguous, though — it deletes the span — so it returns an empty tuple, kept distinct from
    a block holding one blank line, which replaces the span with a blank line.
    """
    matches = _SUGGESTION_RE.findall(body)
    if len(matches) != 1:
        return None
    content = str(matches[0])
    if content == "":
        return ()
    return tuple(content[:-1].split("\n") if content.endswith("\n") else content.split("\n"))


def is_allowed_path(path: str) -> bool:
    """Whether a finding's path is one this may write.

    Rejects traversal and absolute paths outright before the glob match, since ``fnmatch`` happily
    matches ``docs/../../etc/x.md`` against ``docs/*.md``.
    """
    if path.startswith("/") or ".." in Path(path).parts:
        return False
    return any(fnmatch.fnmatch(path, pattern) for pattern in PATH_ALLOWLIST)


def _int_or(value: Any, fallback: int) -> int:
    """A comment field that GitHub sends as an int or omits — never trusted to be either."""
    return value if isinstance(value, int) else fallback


def findings_from_comments(
    comments: list[dict[str, Any]], *, reviewer_login: str = REVIEWER_LOGIN
) -> tuple[list[Finding], list[Refused]]:
    """The applicable wording findings on a pull request, and the marked ones it had to refuse.

    A comment that is not the reviewer's, is a reply, or carries no prose marker is not a finding at
    all and is passed over silently. One that *is* a marked finding but cannot be applied
    mechanically comes back as a ``Refused`` with its reason, so the caller can report it.
    """
    findings: list[Finding] = []
    refused: list[Refused] = []
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") != reviewer_login or user.get("type") != "Bot":
            continue
        if comment.get("in_reply_to_id") is not None:
            continue
        body = str(comment.get("body") or "").replace("\r\n", "\n")
        if not body.startswith(CLAUDE_PREFIX) or PROSE_MARKER not in body:
            continue

        comment_id, path = int(comment["id"]), str(comment["path"])
        if not is_allowed_path(path):
            refused.append(
                Refused(comment_id, path, "outside the documentation and roadmap allowlist")
            )
            continue
        if comment.get("side") not in (None, "RIGHT"):
            continue
        line, original_line = comment.get("line"), comment.get("original_line")
        if not isinstance(line, int) or not isinstance(original_line, int):
            refused.append(
                Refused(comment_id, path, "outdated — its line is no longer in the diff")
            )
            continue
        replacement = extract_suggestion(body)
        if replacement is None:
            refused.append(Refused(comment_id, path, "carries no single `suggestion` block"))
            continue

        start_line = _int_or(comment.get("start_line"), line)
        original_start = _int_or(comment.get("original_start_line"), original_line)
        post_image = parse_diff_hunk(str(comment.get("diff_hunk") or ""))
        expected = [post_image.get(n) for n in range(original_start, original_line + 1)]
        if any(text is None for text in expected):
            refused.append(
                Refused(comment_id, path, "its diff hunk no longer covers the lines it names")
            )
            continue
        if len(expected) != line - start_line + 1:
            # The current span and the posting-time span disagree, so `expected` could never
            # match the file: emitting the finding would report it as drift forever.
            refused.append(
                Refused(comment_id, path, "its line span no longer matches the posted span")
            )
            continue

        findings.append(
            Finding(
                comment_id=comment_id,
                path=path,
                start_line=start_line,
                end_line=line,
                expected=tuple(text for text in expected if text is not None),
                replacement=replacement,
            )
        )
    return findings, refused


def apply_to_lines(
    lines: list[str], findings: list[Finding]
) -> tuple[list[str], list[Finding], list[Refused]]:
    """Splice every finding whose span still matches, and say why each of the rest did not.

    Applied bottom-up so an earlier splice cannot shift a later finding's line numbers; a finding
    that overlaps one already applied therefore fails its own match and is refused rather than
    corrupting the file. A span that already reads as the replacement is reported as such rather
    than as drift — that is the ordinary steady state once a companion PR has merged.
    """
    result = list(lines)
    applied: list[Finding] = []
    refused: list[Refused] = []
    for finding in sorted(findings, key=lambda f: f.start_line, reverse=True):
        start, end = finding.start_line - 1, finding.end_line
        if start < 0 or end > len(result):
            refused.append(Refused(finding.comment_id, finding.path, "points past the file's end"))
            continue
        span = tuple(result[start:end])
        if span != finding.expected:
            reason = (
                "already applied on the source branch"
                if span == finding.replacement
                else "the source pull request has edited that line since the finding was posted"
            )
            refused.append(Refused(finding.comment_id, finding.path, reason))
            continue
        result[start:end] = list(finding.replacement)
        applied.append(finding)
    return result, applied, refused


def pr_body(source_pr: int, applied: list[Finding], refused: list[Refused]) -> str:
    """The companion PR body: what it carries, why it exists, and what it declined to apply."""
    applied_lines = "".join(
        f"- `{f.path}`:{f.start_line} (comment {f.comment_id})\n" for f in applied
    )
    # A finding whose fix has already landed on the source branch is not a problem to report — it is
    # what every run after a merged companion sees.
    outstanding = [r for r in refused if r.reason != APPLIED_ALREADY]
    refused_note = ""
    if outstanding:
        listed = "".join(
            f"- `{r.path}` (comment {r.comment_id}) — {r.reason}\n" for r in outstanding
        )
        refused_note = (
            f"\n**Not applied**, and left for a human rather than guessed at:\n\n{listed}"
        )
    return (
        f"Wording-only fixes for #{source_pr} (BE-0343).\n\n"
        f"The advisory reviewer (BE-0203) posted these as `{PROSE_MARKER}` findings on #{source_pr}. "
        f"Applying them there would re-run that pull request's full CI for a change with no "
        f"behavioral risk, so they land here instead — based on that pull request's own branch, so "
        f"merging this ships the fix while #{source_pr} is still open.\n\n"
        f"**No LLM ran to produce this.** Each change is the finding's own `suggestion` block, "
        f"applied only where the file still matches what the finding saw.\n\n"
        f"Applied:\n\n{applied_lines}{refused_note}\n"
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


def pr_number_from_url(output: str) -> int:
    """The PR number in ``gh pr create``'s output, which is the new pull request's URL."""
    match = _PR_URL_RE.search(output.strip())
    if not match:
        raise ValueError(f"could not read a PR number from `gh pr create` output: {output!r}")
    return int(match.group(1))


def _git(repo_dir: str, args: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", "-C", repo_dir, *args], text=True, capture_output=capture, check=True
    )
    return result.stdout if capture else ""


def _apply_findings(repo_dir: str, findings: list[Finding]) -> tuple[list[Finding], list[Refused]]:
    """Rewrite each touched file in the working tree; report what applied and what did not."""
    applied: list[Finding] = []
    refused: list[Refused] = []
    by_path: dict[str, list[Finding]] = {}
    for finding in findings:
        by_path.setdefault(finding.path, []).append(finding)

    for path, group in by_path.items():
        target = Path(repo_dir) / path
        if not target.is_file():
            refused.extend(
                Refused(f.comment_id, path, "the file is not in the pull request's head")
                for f in group
            )
            continue
        original = target.read_text(encoding="utf-8")
        rewritten, path_applied, path_refused = apply_to_lines(original.split("\n"), group)
        applied.extend(path_applied)
        refused.extend(path_refused)
        if path_applied:
            target.write_text("\n".join(rewritten), encoding="utf-8")
    return applied, refused


def _remote_tip(repo_dir: str, branch: str) -> RemoteTip | None:
    """The companion branch's remote tip, or ``None`` if the branch doesn't exist yet."""
    if not _git(repo_dir, ["ls-remote", "--heads", "origin", branch], capture=True).strip():
        return None
    _git(repo_dir, ["fetch", "origin", branch])
    sha = _git(repo_dir, ["rev-parse", "FETCH_HEAD"], capture=True).strip()
    committer = _git(repo_dir, ["log", "-1", "--format=%ce", "FETCH_HEAD"], capture=True).strip()
    return RemoteTip(sha=sha, committer_email=committer)


def _open_pr_numbers(branch: str) -> list[int]:
    listed = json.loads(
        gh_cli.run(
            ["pr", "list", "--repo", REPO, "--head", branch, "--state", "open", "--json", "number"],
            capture=True,
        )
    )
    return [int(pr["number"]) for pr in listed]


def _open_or_reuse_pr(*, branch: str, base: str, title: str, body: str) -> int:
    """Reuse the open companion PR for this branch, or open one; return its number.

    Opened ready for review, not Draft: it carries only wording changes a human is meant to read and
    merge quickly, and a Draft would add a marking step to that path for no gain.
    """
    existing = _open_pr_numbers(branch)
    if existing:
        # `gh pr edit` selects its target positionally (no --head flag, unlike `gh pr list`).
        gh_cli.run(["pr", "edit", branch, "--repo", REPO, "--body", body])
        print(f"Updated the existing companion PR #{existing[0]} for `{branch}`.")
        return existing[0]
    # `gh pr create` prints the new PR's URL; read the number from that rather than re-listing,
    # which could race the API and fail *after* the PR already exists.
    created = gh_cli.run(
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
        capture=True,
    )
    number = pr_number_from_url(created)
    print(f"Opened companion PR #{number} for `{branch}` against `{base}`.")
    return number


_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved comments(first: 1) { nodes { databaseId } } }
      }
    }
  }
}
"""


def _unresolved_threads(pr: int) -> dict[int, str]:
    """Map each unresolved thread's first comment id to its GraphQL thread id.

    Paginated, because a long-lived pull request runs past one page and a thread missing from the
    map is indistinguishable from one already answered — it would be skipped in silence. Only
    unresolved threads are returned, which is what stops a rerun from replying again to a thread
    this job already answered on an earlier push.
    """
    owner, name = REPO.split("/", 1)
    threads: dict[int, str] = {}
    after: str | None = None
    while True:
        args = [
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
        ]
        if after:
            args += ["-F", f"after={after}"]
        page = json.loads(gh_cli.run(args, capture=True))
        block = page["data"]["repository"]["pullRequest"]["reviewThreads"]
        for node in block["nodes"]:
            comments = node["comments"]["nodes"]
            if not node["isResolved"] and comments:
                threads[int(comments[0]["databaseId"])] = str(node["id"])
        if not block["pageInfo"]["hasNextPage"]:
            return threads
        after = str(block["pageInfo"]["endCursor"])


def _answer_threads(*, pr: int, companion_pr: int, applied: list[Finding]) -> None:
    """Reply to each applied finding's thread naming the companion PR, then resolve it."""
    threads = _unresolved_threads(pr)
    body = reply_body(companion_pr)
    for finding in applied:
        thread_id = threads.get(finding.comment_id)
        if thread_id is None:  # already answered and resolved on an earlier push
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


def _warn_refused(refused: list[Refused]) -> None:
    for entry in refused:
        if entry.reason == APPLIED_ALREADY:
            continue
        print(
            f"::warning::not applying the finding on {entry.path} (comment {entry.comment_id}): {entry.reason}"
        )


def run(args: argparse.Namespace) -> int:
    """Read the findings, apply them to a fresh companion branch, and ship it."""
    comments: list[dict[str, Any]] = json.loads(
        gh_cli.run(["api", "--paginate", f"repos/{REPO}/pulls/{args.pr}/comments"], capture=True)
    )
    findings, refused = findings_from_comments(comments)
    if not findings:
        _warn_refused(refused)
        print(f"No applicable `{PROSE_MARKER}` findings on #{args.pr} (no-op).")
        return 0

    applied, apply_refused = _apply_findings(args.repo_dir, findings)
    refused += apply_refused
    _warn_refused(refused)

    if not applied:
        print(f"Every finding on #{args.pr} was already applied or could not be applied (no-op).")
        return 0
    if not _git(args.repo_dir, ["status", "--porcelain"], capture=True).strip():
        print("The applied text already matches the source branch; nothing to push (no-op).")
        return 0

    branch = f"prose-fix/pr-{args.pr}"
    tip = _remote_tip(args.repo_dir, branch)
    if not is_bot_authored(tip.committer_email if tip else None, args.bot_email):
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
            ["push", f"--force-with-lease={branch}:{tip.sha}", "origin", f"{branch}:{branch}"],
        )

    companion_pr = _open_or_reuse_pr(
        branch=branch,
        base=args.source_branch,
        title=f"docs(prose): wording fixes for #{args.pr}",
        body=pr_body(args.pr, applied, refused),
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

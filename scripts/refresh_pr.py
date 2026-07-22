#!/usr/bin/env python3
"""Turn an AI-authored refresh into a rolling Draft PR, deterministically (BE-0222).

The scheduled ``roadmap-refresh`` / ``docs-refresh`` workflows let the Claude Code action *author*
edits into the working tree; everything after that — deciding what the PR may contain, whether there
is anything to open at all, and never clobbering a human — is deterministic and lives here rather
than in inline YAML, so it is unit-testable and shared by both workflows unchanged. The split mirrors
the prime directive: the LLM authors, this script (and the gate) decide and act.

It runs in two phases, one subcommand each, so the workflow can run the heavy ``make check`` gate
*only* when there is actually something to open, and can run it on exactly the tree the PR will carry:

- ``enforce`` — restore any edit outside the workflow's hard **path allowlist** (the AI can stray,
  but the PR provably cannot carry a disallowed path; a stray is surfaced loudly, never swallowed),
  then report whether any allowed change remains and which strays were discarded. The caller runs
  the gate and ``publish`` only when something allowed changed, *after* this restore — so the gate
  validates the same tree the PR ships, and a quiet day opens or touches no PR.
- ``publish`` — guard the rolling branch against clobbering a human's work: force-update only when
  the branch's remote tip was committed by the automation bot itself, and even then with
  ``--force-with-lease`` against the fetched tip so a human push landing in the read→push window
  fails loudly instead of being overwritten; a brand-new branch is created with a plain
  (non-force) push, which likewise fails loudly if someone raced it into existence. (The same
  *reason* `check_stale_roadmap_prs.py` guards its fix branch, by a mechanism suited to a single
  shared rolling branch.) Then push the reconciliation and open (or reuse) one always-Draft PR,
  recording the in-job ``make check`` result and any discarded strays in the body. Only a human
  ever marks it ready and merges.

The list of discarded strays travels ``enforce`` → ``publish`` through a plain newline-delimited
file (``--strays-file``), never a shell-interpolated workflow value: the paths are AI-authored, so
routing them through ``${{ }}`` into a ``run:`` block would be a script-injection sink.

The git/``gh`` orchestration calls the network, so — like ``check_stale_roadmap_prs.py`` and
``sync_roadmap_tracking_issues.py`` — it never runs inside ``make check``; the tests cover the pure
parts (porcelain parsing, allowlist partitioning, the clobber decision, the body text).

Usage::

    python scripts/refresh_pr.py enforce --allow 'roadmaps/**'      # writes changed/strays to $GITHUB_OUTPUT
    python scripts/refresh_pr.py publish --branch chore/roadmap-refresh --base main \
        --label roadmap --title "chore(roadmap): refresh BE status/progress" \
        --bot-email "app-slug[bot]@users.noreply.github.com" --check-result pass --allow 'roadmaps/**'
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_cli

REPO = "bajutsu-e2e/bajutsu"


@dataclass(frozen=True)
class Change:
    """One entry from ``git status --porcelain``: the affected path and whether it is untracked."""

    path: str
    untracked: bool


@dataclass(frozen=True)
class RemoteTip:
    """The rolling branch's remote tip: its SHA (for the lease) and its committer email (the owner)."""

    sha: str
    committer_email: str


def parse_porcelain(output: str) -> list[Change]:
    """The changed paths in ``git status --porcelain`` output.

    Handles the shapes a refresh actually produces — modified/added/deleted files (the agent's ``Edit``
    rewrites tracked ones in place; its ``Write`` creates untracked ones).
    Read with ``core.quotePath=false`` (see ``_git_status``) so non-ASCII paths arrive literal, not
    octal-escaped.

    A rename (``R``) is kept only as its destination path, and ``_restore`` would not fully undo a
    *disallowed* rename (it can't see the vanished source). This is sound only because a rename can't
    occur here: the agent's allowed tools (``Read``/``Grep``/``Glob``/``Edit``/``Write`` + a fixed
    ``Bash`` allowlist) have no move/delete, so nothing produces an ``R`` status. If that toolset ever
    gains a move/delete, ``_restore`` must track both sides of a rename before this stays safe.
    """
    changes: list[Change] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        status, path = raw[:2], raw[3:]
        if " -> " in path:  # rename/copy: "old -> new"; the new path is what the PR carries
            path = path.split(" -> ", 1)[1]
        changes.append(Change(path=path, untracked=status == "??"))
    return changes


def partition(changes: list[Change], allowlist: list[str]) -> tuple[list[Change], list[Change]]:
    """Split changes into (allowed, disallowed) by matching each path against the glob allowlist.

    An empty allowlist disallows everything — a misconfigured workflow is fail-safe: it can restore
    every edit and open nothing, never open a PR carrying arbitrary paths. ``fnmatch`` treats ``*`` as
    crossing ``/``, which is exactly right for the configured whole-subtree allowlists (``roadmaps/**``,
    ``docs/**``, ``DESIGN.md``); a narrower future pattern would need a stricter matcher.
    """
    allowed: list[Change] = []
    disallowed: list[Change] = []
    for change in changes:
        if any(fnmatch.fnmatch(change.path, pattern) for pattern in allowlist):
            allowed.append(change)
        else:
            disallowed.append(change)
    return allowed, disallowed


def is_bot_authored(remote_tip_committer: str | None, bot_email: str) -> bool:
    """Whether the rolling branch is safe to force-update.

    Safe when the branch does not yet exist (``remote_tip_committer is None``) or its remote tip was
    committed by the automation bot. A human-committed tip is *not* safe — the run must skip rather
    than force over it. The committer (not author) email is what identifies who last wrote the tip:
    author survives a human's amend/rebase, committer does not.
    """
    return remote_tip_committer is None or remote_tip_committer == bot_email


def pr_body(label: str, base: str, check_result: str, strays: list[str]) -> str:
    """The Draft PR body: what this is, that a human is the sole merger, the gate result, and — for
    the human who is the only decision-maker here — any edits the author attempted outside its
    allowlist and had discarded (a repeated attempt to reach product code is worth seeing)."""
    gate = "✅ passed" if check_result == "pass" else "⚠️ failed (see the PR's own `check` CI)"
    stray_note = ""
    if strays:
        listed = ", ".join(f"`{s}`" for s in strays)
        stray_note = (
            f"- ⚠️ The author also attempted edits outside its path allowlist, which were "
            f"discarded before this PR: {listed}\n"
        )
    return (
        f"Rolling **{label}-refresh** Draft PR (BE-0222).\n\n"
        f"A scheduled workflow used the Claude Code action to *author* a reconciliation of the "
        f"drift a deterministic gate cannot catch, then opened this Draft PR. **No LLM is on the "
        f"`run`/CI verdict path** — this is purely an authoring aid, held to the same `make check` "
        f"as any change, and **only a human reviews, marks ready, and merges it**.\n\n"
        f"- In-job `make check`: {gate}\n"
        f"- Base: `{base}`\n"
        f"{stray_note}\n"
        f"If the reconciliation looks wrong, close this PR — the next run reopens a fresh one from "
        f"the current `{base}`. Push your own fixups onto the branch and the next run will *skip* "
        f"rather than overwrite them.\n\n"
        f"🤖 Generated by the `{label}-refresh` workflow (BE-0222)."
    )


def _git(args: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(["git", *args], text=True, capture_output=capture, check=True)
    return result.stdout if capture else ""


def _git_status(allowlist: list[str]) -> tuple[list[Change], list[Change]]:
    # core.quotePath=false so a non-ASCII path is emitted literally, not octal-escaped — otherwise it
    # would fail to match the allowlist and be wrongly restored (safe direction, but a broken run).
    out = _git(["-c", "core.quotePath=false", "status", "--porcelain"], capture=True)
    return partition(parse_porcelain(out), allowlist)


def _restore(disallowed: list[Change]) -> None:
    """Undo out-of-allowlist edits so the PR can only ever carry allowed paths."""
    for change in disallowed:
        print(f"::warning::refresh edited a path outside its allowlist; discarding: {change.path}")
        if change.untracked:
            # git clean removes an untracked file *or* directory (porcelain collapses a wholly-new
            # dir to one `dir/` entry, which Path.unlink can't delete); -x ignores .gitignore scope.
            _git(["clean", "-fdx", "--", change.path])
        else:
            _git(["checkout", "--", change.path])


def _set_output(name: str, value: str) -> None:
    """Append a step output, or just print it when run outside Actions (a local invocation)."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    print(f"{name}={value}")


def _remote_tip(branch: str) -> RemoteTip | None:
    """The rolling branch's remote tip (SHA + committer email), or ``None`` if it doesn't exist yet."""
    if not _git(["ls-remote", "--heads", "origin", branch], capture=True).strip():
        return None
    _git(["fetch", "origin", branch])
    sha = _git(["rev-parse", "FETCH_HEAD"], capture=True).strip()
    committer = _git(["log", "-1", "--format=%ce", "FETCH_HEAD"], capture=True).strip()
    return RemoteTip(sha=sha, committer_email=committer)


def open_or_update_pr(*, branch: str, base: str, title: str, body: str) -> None:
    """Reuse the open Draft PR for this branch, or open one; never leaves more than one."""
    existing = json.loads(
        gh_cli.run(
            ["pr", "list", "--repo", REPO, "--head", branch, "--state", "open", "--json", "number"],
            capture=True,
        )
    )
    if existing:
        # `gh pr edit` selects its target positionally (no --head flag, unlike `gh pr list`).
        gh_cli.run(["pr", "edit", branch, "--repo", REPO, "--body", body])
        print(f"Updated the existing Draft PR for `{branch}`.")
    else:
        gh_cli.run(
            [
                "pr",
                "create",
                "--repo",
                REPO,
                "--draft",
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
        print(f"Opened a Draft PR for `{branch}` against `{base}`.")


def _enforce(allowlist: list[str], strays_file: str | None) -> int:
    """Restore out-of-allowlist edits, record the strays, and report drift (a step output)."""
    allowed, disallowed = _git_status(allowlist)
    _restore(disallowed)
    _set_output("changed", "true" if allowed else "false")
    if strays_file:
        # Newline-delimited, not a shell-interpolated output: these paths are AI-authored.
        Path(strays_file).write_text("".join(f"{c.path}\n" for c in disallowed), encoding="utf-8")
    if not allowed:
        print("No allowed changes; the workflow will open no PR (no-op).")
    return 0


def _publish(args: argparse.Namespace) -> int:
    """Guard the rolling branch, push the allowed changes, and open/update the Draft PR."""
    allowed, _ = _git_status(args.allow)
    if not allowed:  # defence in depth: enforce already gated this, but never push an empty commit
        print("No allowed changes to publish (no-op).")
        return 0

    tip = _remote_tip(args.branch)
    if not is_bot_authored(tip.committer_email if tip else None, args.bot_email):
        print(
            f"::warning::`{args.branch}` has a human-committed tip since the last run; "
            f"skipping to avoid clobbering it. Merge or reset the branch to let the refresh resume."
        )
        return 0

    _git(["checkout", "-B", args.branch])
    _git(["add", *(c.path for c in allowed)])  # only allowed paths — never a make-check byproduct
    _git(["commit", "-m", args.title])
    if tip is None:
        # Brand-new branch: a plain (non-force) push creates it, and is rejected loudly if someone
        # raced a branch of that name into existence in the read→push window.
        _git(["push", "origin", f"{args.branch}:{args.branch}"])
    else:
        # Lease against the tip we just inspected: a human push landing in the read→push window makes
        # this fail loudly rather than silently clobbering it.
        _git(
            [
                "push",
                f"--force-with-lease={args.branch}:{tip.sha}",
                "origin",
                f"{args.branch}:{args.branch}",
            ]
        )

    open_or_update_pr(
        branch=args.branch,
        base=args.base,
        title=args.title,
        body=pr_body(args.label, args.base, args.check_result, _read_strays(args.strays_file)),
    )
    return 0


def _read_strays(strays_file: str | None) -> list[str]:
    """The paths ``enforce`` discarded, from its newline-delimited file (empty if none / no file)."""
    if not strays_file or not Path(strays_file).exists():
        return []
    return [line for line in Path(strays_file).read_text(encoding="utf-8").splitlines() if line]


def _split_allow(raw: list[str]) -> list[str]:
    """Flatten the ``--allow`` values (each may itself be a newline-separated block) into globs."""
    return [line.strip() for value in raw for line in value.splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Open/update a refresh Draft PR (BE-0222).")
    sub = parser.add_subparsers(dest="command", required=True)

    enforce = sub.add_parser("enforce", help="restore out-of-allowlist edits; report drift")
    enforce.add_argument(
        "--allow", action="append", default=[], help="allowlist glob(s), one per line"
    )
    enforce.add_argument("--strays-file", help="write discarded paths here (newline-delimited)")

    publish = sub.add_parser("publish", help="push the rolling branch and open/update the Draft PR")
    publish.add_argument(
        "--branch", required=True, help="the rolling branch, e.g. chore/roadmap-refresh"
    )
    publish.add_argument("--base", required=True, help="the PR base branch (main)")
    publish.add_argument("--label", required=True, help="'roadmap' or 'docs' — used in the PR body")
    publish.add_argument("--title", required=True, help="the PR title (used only when creating)")
    publish.add_argument("--bot-email", required=True, help="the automation bot's commit email")
    publish.add_argument("--check-result", choices=["pass", "fail"], required=True)
    publish.add_argument("--strays-file", help="newline-delimited paths enforce discarded")
    publish.add_argument(
        "--allow", action="append", default=[], help="allowlist glob(s), one per line"
    )

    args = parser.parse_args(argv)
    args.allow = _split_allow(args.allow)
    return _enforce(args.allow, args.strays_file) if args.command == "enforce" else _publish(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

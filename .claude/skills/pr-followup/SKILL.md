---
name: pr-followup
model: sonnet
description: >-
  Handle post-PR-creation follow-ups: fix CI failures, address review comments,
  reply to reviewers, and resolve conversations. Use when the user says
  "CIが落ちている", "reviewコメントに対応して", "PRのコメントを確認して",
  "PR #NNN を直して", or asks to fix a failing PR. Reads CI logs and review
  comments, makes targeted fixes, pushes, and replies. If a review comment
  requires a fundamental design change, escalate to the user instead of
  attempting it.
---

# PR follow-up

Fix CI failures and address review comments on an existing PR.
This is a **focused fix-and-reply** skill — scoped to the PR's branch.

Invoked directly, or as one iteration of an automated tail: after
[`implement-be`](../implement-be/SKILL.md) opens the Draft PR, it runs a paced `/loop` that spawns a
fresh subagent each iteration and hands it this skill's steps directly in its prompt (the `Skill`
tool runs only in the main conversation, so the subagent can't invoke `/pr-followup` itself),
driving the PR to quiet and green. The conflict check, the `CHANGES_REQUESTED` stop condition, and
the iteration backstops all live in that loop layer — this skill itself is unchanged and behaves
identically whether looped, run as a subagent, or called by hand.

## Inputs

The user provides one of:
- A PR number (`#NNN`)
- A branch name
- "the current PR" (use the current branch's PR)

## Steps

### 1. Assess the PR state

```bash
gh pr view <PR> --json number,title,headRefName,statusCheckRollup,reviewDecision
gh pr checks <PR>
```

### 2. Fix CI failures (if any)

- Read the failing check's log:
  ```bash
  gh run view <run-id> --log-failed
  ```
- Identify the root cause from the log output.
- Make the targeted fix on the PR's branch.
- Run `make check` locally to verify.
- Push the fix.

### 3. Address review comments

- List unresolved review comments:
  ```bash
  gh api repos/{owner}/{repo}/pulls/<PR>/comments --jq '.[] | select(.position != null) | {id, path, line: .original_line, body}'
  ```
- For each comment:
  1. Read the comment and understand the request.
  2. Make the code change if it's a targeted fix.
  3. Run `make check` to verify.
  4. Reply to the comment stating the outcome and its grounds — the change you made (cite the
     file/line or commit), or, when you decline, the specific reason it does not apply. A bare
     "done" or 👍 is not a reply.
  5. **Resolve the conversation** — whether you fixed the comment or consciously declined it. Every
     answered comment gets both a reply and a resolved conversation, so the open conversations
     always reflect exactly what still needs attention. The only conversations left open are the
     undecided ones you escalate (see below); never resolve a comment whose question is still
     unanswered.

### 4. Self-review against the CI review contract

If step 2 or 3 made a change this iteration, mirror the CI "Claude review" workflow locally before
pushing whatever hasn't shipped yet, following [`ideation`](../ideation/SKILL.md) step 5's
procedure exactly, with three differences: give the subagent a local `git diff` against the PR's
remote branch (so it sees this iteration's not-yet-pushed fixes — unlike `gh pr diff <PR>`, which
only shows what GitHub's remote head already has) plus `gh pr view <PR> --comments` for the
discussion, instead of a fresh diff, and **not** scoped to `roadmaps/` — unlike `ideation`, whose
fixes only ever land there, this skill's fixes can land anywhere the CI failure or review comment
points to; route a genuine design-change finding to this skill's own Escalation section instead of
`ideation`'s, reporting it directly in this iteration's summary rather than leaving a review thread
open, since there is no PR conversation to leave unresolved for a self-review-only finding. Run
`make check` after every fix, the same as steps 2 and 3.

This step pays off most directly for step 3's review-comment fixes, which wait until step 5's push
to go out; a step 2 CI-failure fix already went out with its own push, so here this step is an
extra local check rather than the round-trip savings it buys for step 3 (BE-0203). Skip it entirely
when nothing changed this iteration (e.g. a poll under `implement-be`'s `/loop` where CI is already
green and no new comments arrived), since there is nothing new to self-review or push.

### 5. Push and report

- Push all fixes in one commit (or logical commits if changes are independent).
- Report what was fixed and what remains.

## Escalation

If a review comment asks for a **fundamental design change** (new approach,
architectural rethink, or trade-off the user should weigh), do NOT attempt the
fix. Instead:
- Summarize the request
- Explain why it needs the user's judgment
- Suggest starting an Opus session for the redesign
- Leave the review conversation unresolved until the user decides — this is the one exception to
  the resolve-every-answered-comment rule in step 3.

## What this skill does NOT do

- Rewrite large sections of code
- Make design decisions on behalf of the user
- Force-push or rebase (push incremental fix commits)
- Create new PRs

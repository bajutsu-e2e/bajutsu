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

Mirror the CI "Claude review" job locally, before pushing, instead of waiting on its own
re-review round trip on the next push (BE-0203) — the same discipline
[`ideation`](../ideation/SKILL.md)'s step 5 applies when drafting a BE proposal from scratch,
applied here to a fix instead of a fresh draft. Spawn a fresh subagent (Agent tool) that has
**not** seen this pr-followup session — the actual CI reviewer also runs cold — and give it the
contract at [`.github/claude-review-prompt.md`](../../../.github/claude-review-prompt.md) plus
the PR's current state: `gh pr diff <PR>` and `gh pr view <PR> --comments` (both already available
here, unlike `ideation`'s pre-PR case, so there is no part of the contract to skip). Per the
contract's own rule, it should flag only what this round's fix newly introduces or leaves
unaddressed — not re-raise a point already settled in the discussion it just read.

Fix every finding it raises, unless it is a false positive or a deliberate, already-explained
trade-off (note the rationale and move on) or it calls for a genuine design change (handle it
under this skill's own Escalation section instead of attempting it). Run `make check` after each
fix. Re-run the subagent against the updated diff after non-trivial fixes, capped at 3 rounds — if
it still hasn't converged by then, stop and report what remains rather than looping further.

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

# PR follow-up

Fix CI failures and address review comments on an existing PR.
This is a **focused fix-and-reply** skill — scoped to the PR's branch.

Invoke it directly or as one iteration of the bounded follow-up described by
[`implement-be`](../implement-be/workflow.md). That workflow can hand these steps to a fresh
subagent and drive the PR to quiet and green. Conflict checks, the `CHANGES_REQUESTED` stop
condition, and iteration backstops live in the outer workflow. These steps behave identically
whether run in a subagent or called directly.

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
pushing whatever hasn't shipped yet, following [`ideation`](../ideation/workflow.md) step 5's
two-role procedure exactly — a review/plan pass that classifies findings and never edits, then an
implement pass that applies its instructions (BE-0347) — with three differences. First, give the
review/plan pass a local `git diff` against the
PR's remote branch instead of a fresh diff against `origin/main` — unlike `gh pr diff <PR>`, which
only shows what GitHub's remote head already has, a local diff sees this iteration's not-yet-pushed
fixes — and stage whatever step 2 or 3 touched first (`git add <paths>`), the same guard `ideation`
applies to its own new files, so a file this iteration newly introduced doesn't stay untracked and
skip the diff entirely. Second, don't scope that diff to `roadmaps/` — unlike `ideation`, whose
fixes only ever land there, this skill's fixes can land anywhere the CI failure or review comment
points to. Third, give the review/plan pass `gh pr view <PR> --comments` for the discussion (there
is a live PR here, unlike `ideation`'s pre-PR case), and route a review/plan escalation to this
skill's own Escalation section instead of `ideation`'s, reporting it directly in this iteration's
summary rather than leaving a review thread open, since there is no PR conversation to leave
unresolved for a self-review-only finding. Run `make check` after every fix, the same as steps 2
and 3.

This step pays off most directly for step 3's review-comment fixes, which wait until step 5's push
to go out; a step 2 CI-failure fix already went out with its own push, so here this step is an
extra local check rather than the round-trip savings it buys for step 3 (BE-0203). Skip it entirely
when nothing changed this iteration (for example, a follow-up poll where CI is already
green and no new comments arrived), since there is nothing new to self-review or push.

### 5. Push, request a live review, and report

- Push all fixes in one commit (or logical commits if changes are independent).
- **Request the live review on demand.** The "Claude review" workflow no longer re-reviews on every
  push (BE-0347): it runs automatically only when a pull request opens or reopens, and every later
  pass is requested. So when this iteration pushed something and step 4's self-review came back
  clean, ask for that pass yourself — capturing a timestamp first, because that is how the run is
  found again below:
  ```bash
  REQUESTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  gh pr comment <PR> --body "@claude review"
  ```
  Skip the request when nothing was pushed this iteration, or when step 4 escalated instead of
  clearing — the PR is not yet in a stable state to review. Then confirm the review actually ran,
  checking the **job** rather than the run. The workflow's trusted-actor gate is a job-level `if:`,
  so a comment event creates a workflow run even when the request is dropped: the run appears,
  completed and green, with its `claude review` job merely `skipped`. Run existence proves nothing.
  Pin the run to this request's own comment with `--user`, since any comment in the repository
  creates a run in the same window; filter by creation time rather than `--branch`, since a
  comment-triggered run executes against the default branch and its `head_branch` is never the PR
  branch; and allow a few seconds for the run to be created, retrying rather than querying once —
  a run is not normally listable the instant `gh pr comment` returns:
  ```bash
  for _ in $(seq 1 10); do
    RUN_ID=$(gh run list --workflow "Claude review" --event issue_comment \
      --user "$(gh api user --jq .login)" --created ">$REQUESTED_AT" \
      --json databaseId --jq '.[0].databaseId')
    [ -n "$RUN_ID" ] && break
    sleep 3
  done
  gh run view "$RUN_ID" --json jobs \
    --jq '.jobs[] | select(.name == "claude review") | .conclusion // .status'
  ```
  The review counts as started only when that job is `queued`, `in_progress`, or completed with a
  conclusion other than `skipped`. A dropped request's run completes within seconds with the job
  `skipped`, and the workflow leaves no trace on the pull request when it drops one — so the silence
  is indistinguishable from a review that found nothing, and a follow-up poll would read it as a
  quiet PR. Escalate on `skipped`, or when no run by this account appears.
- Report what was fixed and what remains.

## Escalation

Escalate, rather than pressing on, when step 5 finds its `@claude review` request was dropped — its
run's `claude review` job `skipped`, or no run by this account at all: the live pass a later poll is
waiting for will never arrive, and only a human can grant the trusted-actor association the workflow
requires.

If a review comment asks for a **fundamental design change** (new approach,
architectural rethink, or trade-off the user should weigh), do NOT attempt the
fix. Instead:
- Summarize the request
- Explain why it needs the user's judgment
- Ask the user how to proceed with the redesign
- Leave the review conversation unresolved until the user decides — this is the one exception to
  the resolve-every-answered-comment rule in step 3.

## What this skill does NOT do

- Rewrite large sections of code
- Make design decisions on behalf of the user
- Force-push or rebase (push incremental fix commits)
- Create new PRs

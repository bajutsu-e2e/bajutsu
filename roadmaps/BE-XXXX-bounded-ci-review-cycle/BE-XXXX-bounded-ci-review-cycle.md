**English** · [日本語](BE-XXXX-bounded-ci-review-cycle-ja.md)

# BE-XXXX — Bound the CI review cycle with a local Fable-plan, Sonnet/Opus-fix loop requested on demand

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-bounded-ci-review-cycle.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item splits the local, pre-push review pass that already mirrors the "Claude review" GitHub
Actions contract ([BE-0203][]) into two roles instead of one. A **Fable**-run reviewer and planner
classifies findings against [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md)
and writes fix instructions. A **Sonnet-or-Opus**-run implementer applies those instructions. The
item also narrows the CI workflow's own trigger, from every push to open-or-reopen plus an explicit
on-demand request, and bounds the resulting review-and-fix cycle with a hard round cap. The result is
a pull request whose review comments converge to zero, rather than a live cycle that can, in
practice, run unbounded.

## Motivation

On a real pull request, addressing a "Claude review" finding and pushing the fix often does not
converge. The bot ([`.github/workflows/claude-review.yml`][claude-review.yml]) re-reviews on every
push (`pull_request: types: [opened, synchronize, reopened]`), and it can post another finding on
the fix itself. A fix-push-review cycle can then repeat indefinitely instead of settling. The bounded
follow-up loop that `implement-be` runs to drive a pull request to quiet and green stops only once CI
is green, no review carries `CHANGES_REQUESTED`, and two consecutive polls find no new comment. If the
bot keeps finding something new on every poll, that third condition never arrives, and the loop has
no other exit. This happens even though the review contract has already been tightened twice, to a
strict functional-impact-only severity floor, and the bot already runs on Opus for sharper triage:
those two changes tightened the contract's rules, not the process that lets a live cycle run
unbounded.

The repository already has a mechanism built for this gap: a local self-review pass that
mirrors the same contract before every push. It lives in `ideation` step 5 (the canonical procedure),
in `pr-followup` step 4, and in `propose-and-build` Phase A, and `implement-be` steps 7 and 12
inherit it. The pass already caps itself at three rounds, and its documented reason for that cap
already names this item's complaint: a large-language-model (LLM)-based reviewer is not fully
deterministic, and it could keep surfacing a fresh minor finding each round, possibly one its own
previous fix introduced. Today, though, this pass spawns one fresh subagent that both critiques the
diff against the contract and fixes what it finds. That conflation plausibly drives the churn this
item addresses: a model correcting its own finding has every incentive to patch enough to silence one
comment, leaving something adjacent that a fresh look — its own next round, or the live bot after the
push — flags again. `fable` is a Claude Code model alias this repository's tooling already recognizes
(`tests/test_skill_models.py`'s `KNOWN_ALIASES`), yet no role in the repository uses it. It fits a
judgment-only reviewer and planner that never trades a full fix for a quiet round.

Separating what must change from making it change should let each round close a finding instead of
deferring it, so the local pass converges before a push. That, in turn, should cut down how often the
live bot's own re-review cycle even starts. The residual risk is bounded, not eliminated: even a
sharper local pass cannot guarantee that the live bot never surfaces something new. The follow-up
loop also gains a round cap on the live cycle itself, with escalation to a human once the cap is hit
— the same treatment a merge conflict already gets, rather than an indefinite iteration.

## Detailed design

1. **`ideation` step 5 — split the canonical self-review into two roles.** Today's step spawns one
   subagent that both critiques the diff against every lens in the contract and fixes what it finds.
   Split it. A review/plan pass identifies every finding that clears the severity floor, across every
   lens — the prose-quality lenses and the functional ones alike. It classifies each finding as
   either a concrete fix instruction (file, exact location, exact change) or an escalation for a
   finding that calls for a genuine design change. It never edits a file. A separate implement pass
   applies the fix instructions it is given, with no re-judging of severity and no scope
   creep. If an instruction looks unsafe or wrong, it reports that back rather than silently
   deviating. The loop re-runs a fresh review/plan pass against the updated diff, carrying forward
   this round's dismissed findings and their rationale, and it keeps the existing 3-round cap; the
   cap already counted review passes, not fix attempts, so the count does not change.
2. **`pr-followup` step 4 — inherit the same split.** This step already runs `ideation` step 5's
   procedure with three named differences: a local diff instead of a fresh one against `origin/main`,
   no scoping to `roadmaps/`, and the pull request's own discussion and escalation path. Point it at
   the two-role procedure from unit 1. Adjust its escalation wording from "a genuine design-change
   finding" to "a review/plan escalation," so the classified-output shape matches.
3. **`pr-followup` step 5 — request a live check on demand.** After a push whose unit 2 self-review
   came back clean (not an escalation), post a comment containing `@claude review` on the pull
   request. This requests a live check through the workflow's existing on-demand path, instead of
   relying on the automatic trigger unit 6 removes. That path passes an empty `prompt` today and
   falls through to the action's default `@claude` mention handling, so unit 6 must also supply, on
   comment events, both the contract prompt and the prior-findings input that the "Compute the
   review inputs" step builds only for a `pull_request` event today. Otherwise the requested review
   runs without `.github/claude-review-prompt.md`, its severity floor, its `🤖 **Claude Code**`
   prefix, and the dedup that keeps it from re-posting findings already on the pull request. Skip
   the request when nothing was pushed this
   iteration, or when the self-review escalated instead of clearing — the pull request is not yet in
   a stable state to review. After posting, confirm the requested run actually started — poll
   `gh run list --workflow "Claude review" --event issue_comment --json databaseId,createdAt` for a
   run created after the comment, with no `--branch` filter, since an `issue_comment` run is
   attributed to the default branch rather than the pull request's — and escalate when it did not:
   the workflow drops a request whose commenter is not `OWNER`/`MEMBER`/`COLLABORATOR` without any
   signal on the pull request, and unit 5 would read that silence as a quiet poll.
4. **`propose-and-build` Phase A — inherit the same split by reference.** This phase's self-review is
   a condensed restatement of `ideation` step 5. Point it at unit 1's two-role procedure, instead of
   describing one subagent that both critiques and fixes. Phase B and `implement-be` step 12 already
   reference `ideation` step 5 or `pr-followup` by name, so they inherit units 1 through 3 with no
   separate edit. `implement-be` step 7 does not: it restates the single-agent shape itself ("hand it
   to the fresh subagent as text," and its "two differences from that procedure"), so re-point that
   prose at the two-role procedure and say which role receives the contract text and the
   steps-8-and-10-are-pending caveat.
5. **`implement-be` step 12 — bound the live cycle with a round cap.** The loop's "two consecutive
   quiet polls" stop condition today means "no new review comment from any reviewer, human or bot."
   Keep the human half intact and redefine only the bot half: a poll is quiet when it finds no new
   human review comment, and either no live review was requested this iteration or the one requested
   came back with nothing new. Add a third counter alongside the loop's existing
   Review-wait (20 iterations) and CI-wait (30 iterations) backstops. Increment it whenever an
   iteration reports that its local self-review was clean, a live review was requested, and the next
   poll still found a new review comment; reset it whenever a live review comes back clean. Cap it at
   three, matching the local self-review loop's own round cap. On the third, add this as a new
   escalate condition alongside the loop's existing ones (a `pr-followup` design-change comment, a
   merge conflict, and `CHANGES_REQUESTED` with no inline threads ever left) — stop the loop and hand
   it to a human with a summary, rather than an indefinite iteration.
6. **`.github/workflows/claude-review.yml` — narrow the automatic trigger.** Drop `synchronize` from
   the `pull_request` trigger's `types`, keeping only `opened` and `reopened`. Extend the
   `prose-companion` job's `github.event_name == 'pull_request'` condition to the on-demand comment
   events in the same change — otherwise a wording-only finding raised after the open event has no
   job left to apply it, and BE-0343 stops working for every later review. Widening only that clause
   is not enough: `github.event.pull_request` is null on a comment event, so the job's same-repo
   trust boundary (`head.repo.full_name == github.repository`, what keeps the privileged App token
   away from fork-authored code), its `head.ref` (the companion script's `--source-branch`), and its
   `head.sha` (the companion checkout's `ref`) all evaluate empty. On comment events the job must
   instead re-derive the pull request's head from its number — for example
   `gh pr view --json headRefName,headRefOid,headRepository` — and re-assert the same-repo check
   against that data. Every later check is requested on demand, through the workflow's existing
   `@claude review` comment path (already gated to `OWNER`/`MEMBER`/`COLLABORATOR`), typically
   issued by unit 3. Update the file's own top-of-file comment block to describe this model, and to
   state why the workflow stays rather than being removed: a fork pull request, and any commit made
   outside these Claude Code skills, never goes through the local self-review pass, and would
   otherwise get no review at all. Extend the "Compute the review inputs (prior findings)" step to
   the comment events too: it is gated on `github.event_name == 'pull_request'` today, so an
   on-demand run would otherwise start with an empty prior-findings list and re-post settled
   findings. Widening that gate alone still fails the step: its `PR` env
   (`${{ github.event.pull_request.number }}`) is also empty on a comment event, and the step's own
   `gh api --paginate .../pulls//comments` call errors under `set -euo pipefail` once `PR` is empty.
   The contract prompt unit 3 asks for carries the same dependency twice — `format(...)`'s `{0}`
   fills both `#{0}` and `` gh pr diff {0} `` from the same null value, so an on-demand review would
   be told to diff nothing. All three want the shape the workflow's own concurrency group already
   uses, `github.event.pull_request.number || github.event.issue.number`, which also covers
   `pull_request_review_comment`, where it is `issue.number` that is empty. Note also that
   `cancel-in-progress` is true only for `pull_request`, so two on-demand requests in quick
   succession run concurrently rather than superseding each other.
7. **`docs/ai-development.md` and its Japanese mirror — document the split and the trigger change.**
   Add a subsection to the "Right-sizing the model and reasoning effort" section. Name Fable as the
   local pass's reviewer and planner, state the Sonnet-default, Opus-for-product-code rule for its
   implementer, and record the CI-trigger narrowing with its backstop rationale. Reconcile this with
   the section's existing task-to-capability table, which already lists "PR review" under the medium
   tier (`sonnet`): that entry names the task of reviewing someone else's pull request on request, a
   different task from this item's review/plan role, which only classifies findings inside the local
   self-review loop and never reviews a pull request as a whole; state that distinction explicitly so
   the two entries do not read as conflicting answers to the same question. Draft the Japanese mirror
   fresh, under this repository's Japanese-prose norm, rather than machine-translating it from the
   English.
8. **The four affected `SKILL.md` adapters — pin the concrete models.** Add one line to each of
   `ideation`, `pr-followup`, `propose-and-build`, and `implement-be`'s existing adapter prose. Name
   `fable` for the review/plan role, and `sonnet` or `opus` for the implement role, following the
   task-weight rule this repository's model-tiering convention already applies elsewhere: a fix
   confined to `roadmaps/` or `docs/` stays on the medium tier, and a fix touching product code moves
   to the heavy tier. None of these additions changes a `SKILL.md`'s frontmatter `model:` key, which
   stays each skill's single overall default.

## Alternatives considered

**Keep the automatic per-push trigger, and rely on a sharper local pass alone to reduce what CI
finds.** Rejected. Even a local pass that reaches zero findings on its own contract cannot guarantee
that the live bot's own non-determinism never surfaces a fresh finding on the next push. This
alternative narrows how often the churn happens without bounding it, so the live cycle could still
run unbounded in the worst case.

**Remove the "Claude review" GitHub Actions workflow entirely, once the local pass is trusted.**
Rejected. A fork pull request's `pull_request` run carries no secrets by design, so its only review is
the `@claude review` pass a maintainer requests on demand — the very path that would disappear with
the workflow. A commit made outside these Claude Code skills —
a human pushing directly, for instance — never goes through the local self-review pass either. Both
cases would lose their only review entirely, rather than gaining a less frequent one.

**Cap the live cycle's total iterations in `implement-be`, without splitting the local pass's roles.**
Rejected. A round cap alone stops the symptom, an unbounded loop, without addressing why the local
pass under-fixes in the first place. The model that raised a finding has every incentive to patch
enough of it to move on, and that incentive is a likely reason the live bot keeps finding something
adjacent after each push.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — split `ideation` step 5 into a review/plan pass and an implement pass.
- [ ] Unit 2 — point `pr-followup` step 4 at the two-role procedure.
- [ ] Unit 3 — `pr-followup` step 5 requests a live review on demand after a clean local pass.
- [ ] Unit 4 — point `propose-and-build` Phase A at the two-role procedure.
- [ ] Unit 5 — `implement-be` step 12 gains the live-cycle round cap and escalation.
- [ ] Unit 6 — narrow `claude-review.yml`'s automatic trigger to `opened`/`reopened`.
- [ ] Unit 7 — document the split and the trigger change in `docs/ai-development.md` (+ Japanese).
- [ ] Unit 8 — pin the concrete models in the four affected `SKILL.md` adapters.

## References

- [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) — the review contract
  both the CI workflow and the local self-review pass apply.
- [`.github/workflows/claude-review.yml`][claude-review.yml] — the CI workflow whose trigger this
  item narrows.
- [`docs/ai-development.md`](../../docs/ai-development.md#right-sizing-the-model-and-reasoning-effort-be-0103)
  — the task-to-capability matrix this item's model choices follow.
- [BE-0203][] — the CI reviewer whose contract this item's local pass reuses.
- [BE-0343](../BE-0343-prose-companion-pr/BE-0343-prose-companion-pr.md) — the other consumer of
  this contract's findings, applying wording-only ones to a companion PR.

[BE-0203]: ../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md
[claude-review.yml]: ../../.github/workflows/claude-review.yml

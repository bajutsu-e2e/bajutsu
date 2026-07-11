**English** · [日本語](BE-XXXX-hands-free-implement-review-loop-ja.md)

# BE-XXXX — Hands-free implement-review loop: auto-PR and pr-followup polling in implement-be

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-hands-free-implement-review-loop.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

Extend the [`implement-be`](../../.claude/skills/implement-be/SKILL.md) skill so that, once an
implementation passes the deterministic gate, it drives the whole tail of the change on its own:
it opens a Draft pull request, compacts the session to free context, and then loops the
[`pr-followup`](../../.claude/skills/pr-followup/SKILL.md) skill at a paced interval until the PR
is quiet and green. Today `implement-be` stops at "push to the branch; a human opens the PR", and
every follow-up cycle — reading CI logs, replying to reviewers, resolving conflicts — is triggered
by hand. This item removes those manual hand-offs for the one skill where they are pure overhead:
`implement-be`, whose output is always a self-contained, gate-green change ready to become a PR.

This is a **skill and working-agreement change only** — no product code (`bajutsu/`, `BajutsuKit/`,
runner, drivers) is touched. It sits entirely in the authoring/investigation path, never on the
`run`/CI verdict.

## Motivation

`implement-be` already ends in a well-defined state: the branch is pushed, `make check` is green,
and the roadmap item is flipped to `Implemented`. From there, the remaining work is mechanical and
repetitive:

1. A human opens a Draft PR.
2. CI runs; if it fails, someone re-reads the log and pushes a fix.
3. Reviewers comment; someone answers, edits, and resolves threads.
4. `main` moves; someone rebases and resolves conflicts.

Each of these is exactly what `pr-followup` automates, but firing it is a manual, attention-hungry
poll: the author has to keep checking whether CI finished or a review landed. That polling is both
tedious and a poor use of a long-lived, large-context session — the implement session carries the
full design conversation, so every manual follow-up turn re-reads an expensive context.

Two changes remove the overhead:

- **Auto-PR + auto-followup** turns the hand-offs into one continuous flow: implement → gate →
  Draft PR → follow-up loop, with the human pulled in only when a *decision* is needed (a review
  comment that demands a design change, per `pr-followup`'s existing escalation rule).
- **Compact before the loop** cuts the token cost of that flow. The implement phase's context (the
  design back-and-forth, the file reads) is dead weight once the PR exists; compacting before
  entering the follow-up loop lets each polling turn run against a lean context instead of the full
  implement transcript. Token economy on long sessions is a standing concern for this project.

Scope is deliberately narrow — **`implement-be` only**. The BE-authoring skills
([`ideation`](../../.claude/skills/ideation/SKILL.md), and the proposal phase of
[`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md)) must *not* auto-PR: a
proposal PR is a human review checkpoint, and its BE id is only allocated after a human merges it
([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)). The
working agreement in [`CLAUDE.md`](../../CLAUDE.md) is updated to state this split explicitly.

## Detailed design

The work is a set of documentation/agreement edits — no executable product code. The units below
are MECE.

### Unit 1 — `implement-be` step 10: auto-open a Draft PR after the gate

Replace the current "PR only when asked" step with an **auto-PR** step. Once step 9's `make check`
is green and the branch is pushed:

- Open a Draft PR with `gh pr create --draft`, English title/body per the existing conventions
  (`[BE-NNNN]` prefix, thorough body from the template, `make check` verification line).
- Fill the `Implementing PR:` row in both BE language files with the real number and push that
  follow-up (the existing step-10 requirement, now unconditional).
- The "don't open a PR unless the user asks" caveat is removed **for this skill**. The Draft +
  never-mark-ready-while-red rules from `CLAUDE.md` still hold: the PR is created as a Draft and is
  only marked ready by the human (or a later explicit step), never automatically while CI is red.

### Unit 2 — compact the session before entering the loop

Between opening the PR (Unit 1) and starting the follow-up loop (Unit 3), the skill instructs the
session to **compact** (the harness `/compact`), so the follow-up polling runs against a lean
context rather than the full implement transcript. The rationale (token economy) is recorded inline
so a future editor does not "optimize" the compact away. The design conversation and file reads
from the implement phase are not needed by the follow-up loop; the PR, its branch, and the BE files
are the only state that carries forward.

### Unit 3 — the paced pr-followup loop and its stop conditions

After compacting, `implement-be` instructs the session to invoke the built-in **[`/loop`
skill](../../.claude/skills/loop/)** with `pr-followup` as its target — concretely, it tells the
session: *"Run `/loop /pr-followup #NNN`"*. The `/loop` skill drives the pacing: it invokes
`pr-followup` once per iteration and uses `ScheduleWakeup` (the harness's self-pacing mechanism for
`/loop` dynamic mode) to sleep between iterations. Pacing follows the standard cache-window
guidance: a shorter interval while CI is actively running (waiting on a run to finish), a longer
interval while waiting on human review.

The loop **stops** only when **all** of these hold:

1. **CI is green** — every required check passing.
2. **No merge conflict** — the branch is cleanly mergeable into `main`.
3. **Two consecutive polls with no new review comments** — the review surface has gone quiet (one
   empty poll is not enough; the second confirms quiescence).

When the loop stops under these conditions, **Draft → Ready is a deliberate human step**. The loop
reports that the PR has reached a quiet-and-green state, but it does not call `gh pr ready` itself.
This preserves a human final-sign-off checkpoint before the PR enters the merge queue: the human
can inspect the conversation, confirm no subtle reviewer concern was left unaddressed, and mark it
ready when satisfied. The "hands-free" claim covers the mechanical tail (CI fixes, replying to
comments, rebasing); the merge decision stays with the human.

The loop also **halts and escalates to the human** the moment `pr-followup` hits a comment that
requires a design or spec change — that is `pr-followup`'s existing, unchanged escalation rule, and
it takes priority over the stop conditions above (a design decision is the human's, not the loop's).
A bounded backstop (a maximum number of iterations / wall-clock ceiling) prevents an unbounded loop
if the PR never converges; on hitting it, the skill stops and reports state rather than looping
forever.

Prime-directive check: nothing here puts an LLM on the `run`/CI verdict. `pr-followup` fixes are
still judged by `make check` and CI; the loop only *schedules* those deterministic checks and
answers reviewers. The escalation rule keeps every genuine decision with the human.

### Unit 4 — align `CLAUDE.md` with the new split

Update the working agreement's PR rules so the two paths are stated explicitly and consistently:

- **BE-creation work** (a proposal PR from `ideation` / the proposal phase of `propose-and-build`):
  do **not** auto-create the PR — push and wait for the human, as today. The reason (a proposal is a
  human checkpoint; the id is allocated on merge) is spelled out.
- **Non-BE-creation implementation** (`implement-be`): **auto-create the Draft PR, then run the
  pr-followup loop**, per Units 1–3.

The existing "PRs created by Claude Code always start as Draft" and "never mark ready while red"
rules are preserved and cross-referenced, so the new auto-PR path inherits them.

### Unit 5 — reflect the change where the skills reference each other

`implement-be`'s References and the `pr-followup` skill's framing gain a short note that the two now
compose into an automated tail (implement → PR → followup loop) for `implement-be`, so a reader of
either skill discovers the flow. No other skill changes behavior.

## Alternatives considered

- **Auto-PR for every skill (including `ideation` / `propose-and-build`).** Rejected: a proposal PR
  is a deliberate human checkpoint and its id is allocated only on human merge (BE-0089).
  Auto-opening it would erode that checkpoint. Narrowing to `implement-be` keeps the automation
  where the output is unambiguously ready.
- **Skip the compact; just loop.** Rejected on token economy: the implement transcript is large and
  irrelevant to follow-up, so every polling turn would re-read expensive dead context. Compacting
  once at the hand-off is a cheap, large saving.
- **Stop the loop on the first quiet poll (CI green + no conflict + zero new comments once).**
  Rejected as too eager: a single empty poll can race a reviewer who is mid-comment. Requiring two
  consecutive quiet polls is the agreed, slightly-conservative stop.
- **A fixed, single poll interval.** Rejected: CI-wait and review-wait have very different natural
  cadences. Adaptive pacing (short while CI runs, long while awaiting review) respects the harness's
  cache-window guidance and avoids both wasteful fast polling and sluggish response.
- **Build this as product code / a CI bot instead of a skill.** Rejected as out of scope and heavier
  than needed: the flow is an agent working agreement, and `pr-followup` already exists. Keeping it
  in the skills avoids any new service and keeps every fix judged by the existing gate.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `implement-be` step 10 rewritten to auto-open a Draft PR after the gate.
- [ ] Unit 2 — compact-before-loop step added with its token-economy rationale.
- [ ] Unit 3 — paced pr-followup loop with the three stop conditions + escalation + backstop.
- [ ] Unit 4 — `CLAUDE.md` PR rules split into BE-creation vs. implementation paths.
- [ ] Unit 5 — cross-references between `implement-be` and `pr-followup` updated.

## References

- [`implement-be`](../../.claude/skills/implement-be/SKILL.md) — the skill this item extends (step 10).
- [`pr-followup`](../../.claude/skills/pr-followup/SKILL.md) — the skill the loop invokes each iteration.
- [`ideation`](../../.claude/skills/ideation/SKILL.md) · [`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md)
  — the BE-authoring skills explicitly excluded from auto-PR.
- [`CLAUDE.md`](../../CLAUDE.md) — the working agreement whose PR rules Unit 4 updates.
- [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) — merge-time
  BE-id allocation, the reason proposal PRs stay a human checkpoint.

**English** · [日本語](BE-0230-hands-free-implement-review-loop-ja.md)

# BE-0230 — Hands-free implement-review loop: auto-PR and pr-followup polling in implement-be

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0230](BE-0230-hands-free-implement-review-loop.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0230") |
| Implementing PR | [#932](https://github.com/bajutsu-e2e/bajutsu/pull/932) |
| Topic | Contributor workflow |
| Related | [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) |
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
4. `main` moves; a conflict may appear — someone rebases when it does.

Steps 1–3 are exactly what `pr-followup` can automate, but firing it is a manual,
attention-hungry poll: the author has to keep checking whether CI finished or a review landed.
Step 4 (rebase) stays a human step even in this proposal, because the bajutsu-specific
`pr-followup` skill explicitly does not rebase or force-push. A conflict is detected and
escalated immediately; the human rebases and re-enters the loop. That polling is both
tedious and a poor use of a long-lived, large-context session — the implement session carries the
full design conversation, so every manual follow-up turn re-reads an expensive context.

Two changes remove the overhead:

- **Auto-PR + auto-followup** turns the hand-offs into one continuous flow: implement → gate →
  Draft PR → follow-up loop, with the human pulled in only when a *decision* is needed (a review
  comment that demands a design change, a conflict-triggered rebase, or the final Draft → Ready
  call, per `pr-followup`'s existing escalation rule and Unit 3's stop conditions).
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
  Exception: if the `implement-be` output is itself documentation-only (skills, `CLAUDE.md`,
  roadmap prose — no product code), `CLAUDE.md`'s existing "documentation-only PRs open Ready for
  review" rule takes precedence: the PR is opened Ready (not Draft), with `steering-committee` as
  reviewer. This exception is exactly the situation this item's own implementation will land in.

### Unit 2 — keep the follow-up loop lean by isolating it in subagents

The follow-up work does not need the implement phase's context (the design conversation, the file
reads); the PR, its branch, and the BE files are the only state that carries forward. Running the
token-heavy follow-up work on top of the full implement transcript, every iteration, is exactly the
cost the project's token economy cares about on a long-lived session.

The intent recorded by the original proposal was "compact once at the hand-off point," leaving the
concrete invocation path open. Implementation resolved that open question against `/compact`: **a
skill cannot issue a slash command mid-execution**
([claude-code#68629](https://github.com/anthropics/claude-code/issues/68629)), so the loop cannot
compact itself. (An *external* SDK driver can send `/compact` as input — see *Alternatives
considered* — but that is a different, heavier execution model than the session-local `/loop` this
item deliberately chose.) The same token-economy benefit is instead obtained **structurally**: each
iteration's `pr-followup` work runs in a **fresh subagent context** (Unit 3), so the expensive
investigation — reading CI logs, diffs, and review comments, making fixes — never carries the
implement transcript or the previous iteration's logs. The loop layer that stays in the session
does almost nothing per turn (one `gh` call, then it reads the subagent's short summary), so its own
turns stay light. The rationale is recorded inline so a future editor does not "optimize" the
isolation away.

### Unit 3 — the paced pr-followup loop and its stop conditions

`implement-be` instructs the session to invoke the built-in **`/loop` skill** to pace the follow-up,
and to **delegate each iteration's work to a subagent** (Unit 2) — concretely, it tells the session:
*"Run `/loop run pr-followup for #NNN`"*. The `/loop` skill drives the pacing with `ScheduleWakeup`
(the harness's self-pacing mechanism for `/loop` dynamic mode); each iteration, the loop layer
checks `mergeable`, spawns a subagent (the `Agent` tool) with `pr-followup`'s steps handed to it in
its prompt (the `Skill` tool runs only in the main conversation, so a subagent cannot invoke
`/pr-followup` itself), and reads the subagent's short summary to evaluate the stop conditions. Pacing follows the
standard cache-window guidance: a shorter interval while CI is actively running (waiting on a run to
finish), a longer interval while waiting on human review.

The loop **stops** only when **all** of these hold:

1. **CI is green** — every required check passing. A required check that only a human can satisfy
   (e.g. a required approval count, such as the "two approvals for BE proposals" gate) never goes
   green from the loop; when that is the *only* thing left red and the review surface is quiet, the
   loop treats the PR as quiet-and-green-pending-approval and stops, reporting what still awaits the
   human, rather than spinning until the CI-wait cap fires.
2. **No outstanding "Request changes" review decision** — `reviewDecision` is not
   `CHANGES_REQUESTED`. A reviewer can submit a top-level "Request changes" review with no new
   inline comments; the loop must not stop while that standing veto sits unaddressed, even if the
   two-quiet-poll condition is met. Note: `pr-followup` reads inline comments only (filtered by
   `position != null`) and does not read `/pulls/{pr}/reviews` — so a top-level-body-only
   objection is invisible to it. The loop layer discriminates by history, because two different
   situations present as the same polled state (`CHANGES_REQUESTED`, zero active inline threads):
   if **no inline threads were ever left** to resolve, it **escalates immediately** (same as a
   merge conflict) — there is nothing for `pr-followup` to act on. If inline threads **were** left
   and have since all been resolved but the decision still stands, it **nudges instead** — because
   GitHub clears a `CHANGES_REQUESTED` decision only when the *same reviewer* re-reviews or a
   maintainer dismisses the stale review (resolving inline threads alone does not clear it), the
   loop posts a PR comment (via `gh pr comment`) asking the reviewer to re-review, but only once
   per stale-review episode (e.g., skip re-posting while it is still the PR's most recent comment),
   so a reviewer who is away for a few hours isn't paged by an identical comment on every 20–30
   minute poll.
3. **Two consecutive polls with no new review comments** — the review surface has gone quiet (one
   empty poll is not enough; the second confirms quiescence).

When the loop stops under these conditions, **Draft → Ready is a deliberate human step**. The loop
reports that the PR has reached a quiet-and-green state, but it does not call `gh pr ready` itself.
This preserves a human final-sign-off checkpoint before the PR enters the merge queue: the human
can inspect the conversation, confirm no subtle reviewer concern was left unaddressed, and mark it
ready when satisfied. The "hands-free" claim covers the mechanical tail (CI fixes, replying to
comments); rebasing and the merge decision stay with the human.

The loop also **halts and escalates to the human** in two additional cases:
- `pr-followup` encounters a comment that requires a design or spec change (its existing, unchanged
  escalation rule — a design decision is the human's, not the loop's, and takes priority over the
  stop conditions above).
- A **merge conflict** is detected. This check is owned by the **loop layer in `implement-be`**,
  not by `pr-followup` itself (Unit 5 leaves `pr-followup` unchanged, and today's `pr-followup`
  does not query `mergeable`). At the start of each iteration the loop queries
  `gh pr view --json mergeable`; if the result is `CONFLICTING`, it escalates immediately without
  invoking `pr-followup`. Only `CONFLICTING` escalates: GitHub computes mergeability
  asynchronously and returns `UNKNOWN` while that is in flight (e.g. right after a push), so the
  loop treats `UNKNOWN` as "no conflict yet — proceed to `pr-followup`" and re-checks on the next
  iteration, catching a real conflict once GitHub resolves it. The bajutsu-specific `pr-followup`
  skill explicitly does not rebase or force-push; once the human rebases and resolves the conflict,
  the loop can be restarted.

Two bounded backstops prevent an unbounded loop:

- **Review-wait cap: 20 iterations** (≈ 7–10 h at the recommended 20–30 min cadence; ≤ 20 h at
  the 3600 s `ScheduleWakeup` maximum). Counts only iterations spent waiting on human review, not
  CI-wait polls.
- **CI-wait cap: 30 iterations** without CI turning green. This catches the case where CI stays
  red for a reason `pr-followup` cannot resolve — flaky infrastructure, an unrelated external
  failure, or a fix that does not take effect — and `pr-followup`'s own escalation rule (which
  fires only on a design/spec-change comment) would not trigger. CI-wait and review-wait
  iterations are counted separately. **Classification rule**: an iteration counts as CI-wait
  whenever any required check is not yet green (regardless of review activity); it counts as
  review-wait otherwise. This means the common post-open-PR state — CI running and no review yet —
  counts as CI-wait, not review-wait.

On hitting either cap, the skill stops and reports the current state (CI status, open comment
count). The human can interrupt or restart the loop at any time by stopping the session.
Session-local `/loop` (rather than a scheduled cloud agent) is the chosen mechanism because it is
simpler — no cloud setup or separate scheduling context — and interruptible at any point.

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
- **`/compact` once at the hand-off (the original intent).** Not available: a skill cannot issue a
  slash command mid-execution ([claude-code#68629](https://github.com/anthropics/claude-code/issues/68629)),
  so the loop cannot compact itself. Unit 2 keeps the token-economy goal but reaches it structurally
  — each iteration's `pr-followup` runs in a fresh subagent context, so the implement transcript is
  never re-read by the expensive work.
- **Drive the loop from an external SDK program that sends `/compact` between iterations.** The
  Claude Agent SDK *does* honor `/compact` sent as input (`query({prompt: "/compact", options:
  {continue: true}})`, emitting a `compact_boundary` system message), so this would work. Rejected
  for the same reason as the CI-bot alternative below: it is a separate, heavier execution model
  (an external orchestrator with its own scheduling context), whereas this item deliberately chose
  the simpler, interruptible session-local `/loop`. Recorded as the path to revisit if a future
  item moves the loop out of the session.
- **Skip the isolation; just loop in-session.** Rejected on token economy: the implement transcript
  is large and irrelevant to follow-up, so running the expensive per-iteration investigation on top
  of it would re-read dead context every time. Subagent isolation is a cheap, large saving.
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

- [x] Unit 1 — `implement-be` step 10 rewritten to auto-open a Draft PR after the gate.
- [x] Unit 2 — follow-up loop kept lean by isolating each iteration's `pr-followup` in a fresh subagent context (token-economy rationale recorded), since a skill cannot self-`/compact` (claude-code#68629).
- [x] Unit 3 — paced loop that delegates each iteration to a subagent, with three stop conditions (CI green + no CHANGES_REQUESTED + two quiet polls) + escalation triggers (design change / conflict / CHANGES_REQUESTED with no threads to act on) + two backstops (20 review-wait iterations + 30 CI-wait iterations, counted separately).
- [x] Unit 4 — `CLAUDE.md` PR rules split into BE-creation vs. implementation paths.
- [x] Unit 5 — cross-references between `implement-be` and `pr-followup` updated.

Log:

- [#932](https://github.com/bajutsu-e2e/bajutsu/pull/932) — All five units landed together:
  `implement-be` step 10 became steps 10–12 (auto Draft PR → lean-loop setup → paced `/loop` that
  delegates each iteration to a subagent, with its stop conditions, escalation triggers, and
  backstops), `pr-followup` gained a framing note, `CLAUDE.md` PR rules split into BE-creation vs.
  implementation paths, and the two skills now cross-reference the composed tail. During review on
  this PR, Unit 2 was reworked: the original `/compact`-at-the-hand-off intent turned out to be
  unreachable from a skill (claude-code#68629 — a skill cannot issue a slash command mid-execution),
  so the token-economy goal is met structurally by running each iteration's `pr-followup` in a fresh
  subagent context instead. The SDK-driven-`/compact` path is recorded under *Alternatives
  considered* as the option to revisit if the loop ever moves out of the session.

## References

- [`implement-be`](../../.claude/skills/implement-be/SKILL.md) — the skill this item extends (steps 10–12).
- [`pr-followup`](../../.claude/skills/pr-followup/SKILL.md) — the skill the loop invokes each iteration.
- [`ideation`](../../.claude/skills/ideation/SKILL.md) · [`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md)
  — the BE-authoring skills explicitly excluded from auto-PR.
- [`CLAUDE.md`](../../CLAUDE.md) — the working agreement whose PR rules Unit 4 updates.
- [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) — merge-time
  BE-id allocation, the reason proposal PRs stay a human checkpoint.

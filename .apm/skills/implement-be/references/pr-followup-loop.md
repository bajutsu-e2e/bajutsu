# Step 12 — the follow-up loop's stop conditions, escalations, and caps

Read this at step 12, once the Draft PR is open and the `/loop` is about to start. The skill body
describes what one iteration does; this file holds the rules that decide when the loop ends.

## Stop the loop only when all three hold

1. **CI green** — every required check passing. A required check that only a human can satisfy
   (e.g. a required approval count) never goes green from the loop; when that is the *only* thing
   left red and the review surface is quiet, treat the PR as quiet-and-green-pending-approval and
   stop, reporting what still awaits the human, rather than burning iterations until a cap.
2. **No `CHANGES_REQUESTED`** — `reviewDecision != CHANGES_REQUESTED`. A top-level "Request
   changes" review can carry no inline comments, and `pr-followup` reads only inline comments
   (`position != null`), so it can't see such a standing veto — the loop layer must. Discriminate
   by history: if `CHANGES_REQUESTED` is set and **no inline threads were ever left** to resolve,
   escalate immediately (like a conflict) — there is nothing for `pr-followup` to act on. If inline
   threads **were** left and the subagent has since resolved every one but the decision still
   stands — GitHub clears `CHANGES_REQUESTED` only when the *same reviewer* re-reviews, not when
   threads are resolved — post **one** nudge (`gh pr comment`) asking the reviewer to re-review,
   once per stale-review episode (skip re-posting while your nudge is still the latest comment), so
   an away reviewer isn't paged on every poll.
3. **Two consecutive quiet polls** — no new review comments across two polls in a row (one empty
   poll can race a reviewer mid-comment; the second confirms quiescence). Read the bot half of
   "quiet" against what was actually requested, since the live reviewer no longer runs on every push
   (BE-0347): a poll is quiet when it finds no new **human** review comment, and either no live
   review was requested this iteration or the one requested came back with nothing new.

When all three hold, **report that the PR is quiet-and-green, and stop — do not call
`gh pr ready`.** Draft → Ready is a deliberate human sign-off: the human inspects the conversation,
confirms no subtle concern was left unaddressed, and marks it ready. "Hands-free" covers the
mechanical tail (CI fixes, replies), not the merge decision or a rebase.

## Carry every pending draft into the final report

An iteration's summary may carry a **pending draft** — a GitHub Issue that
[`record-issue`](../../record-issue/SKILL.md) drafted for an out-of-scope finding but could not
file, because no human was in the turn to approve it
([BE-0384](../../../../roadmaps/BE-0384-record-issue-skill/BE-0384-record-issue-skill.md)). A
pending draft is **not** an escalation and never stops the loop. The loop only collects it.

Collect the drafts as the iterations return them, and carry every one into the loop's own final
report, **deduplicated against the drafts earlier iterations already returned**. Each iteration is a
fresh subagent that re-notices the same finding, so the same draft arrives again and again. The
report therefore holds one entry per finding rather than one per iteration, and a draft returned
early in a long run still reaches the human. Approving one resumes `record-issue` at its step 5
against the draft the human actually saw, rather than re-drafting the issue.

## Escalate (stop and hand to the human) on any of

- a `pr-followup` comment that needs a **design or spec change** (its existing, unchanged
  escalation rule — a design call is the human's, and outranks the stop conditions above);
- a **merge conflict** (the `mergeable` check in the skill body);
- `CHANGES_REQUESTED` with **no inline threads ever left to act on** (stop condition 2's
  never-had-threads branch — the resolved-threads branch nudges instead, so this covers only the
  case where `pr-followup` has nothing to fix);
- **three churn rounds** against the live reviewer (BE-0347). Increment a third counter whenever an
  iteration reports that its local self-review was clean, a live review was requested, and the next
  poll still found a new **bot** review comment; reset it whenever a requested live review comes back
  clean. A new *human* comment is not churn — that is review working, and stop condition 3 already
  keeps the loop running for it. Three such bot-churn rounds means the local pass and the live
  reviewer keep disagreeing, and a fourth is unlikely to settle what three did not — the cap matches
  the local self-review loop's own 3-round cap, and holds for the same reason: an LLM-based reviewer
  is not fully deterministic and can keep surfacing a fresh marginal finding, possibly one its own
  previous fix introduced.

## Two backstops on the loop's total length

They bound the loop alongside the churn counter above (which escalates rather than merely stopping).
Count the two kinds of iteration **separately**, classifying each as **CI-wait whenever any required
check is not yet green** (so the common post-open state — CI running, no review yet — counts as
CI-wait) and **review-wait otherwise**:

- **Review-wait cap — 20 iterations** (approximately 7–10 hours at the 20–30 minute cadence).
- **CI-wait cap — 30 iterations** without CI turning green — catches CI stuck red for a reason
  `pr-followup` can't fix (flaky infra, an unrelated external failure) that its escalation rule
  (which fires only on a design-change comment) wouldn't catch.

On hitting either cap, stop and report the current state (CI status, open comment count). The human
can interrupt or restart the loop at any time by stopping the session.

**Prime-directive check:** no LLM touches the `run`/CI verdict. `pr-followup`'s fixes are still
judged by `make check` and CI; the loop only *schedules* those deterministic checks and answers
reviewers, and every genuine decision escalates to the human.

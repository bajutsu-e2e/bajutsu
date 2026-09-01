---
name: fix-issue
model: opus
description: Ship a plain GitHub issue — a small bug, papercut, or scoped improvement with no roadmap item — end to end, including tests, verification, a Draft PR, and follow-up.
---

# Fix a GitHub issue

Take one plain GitHub issue — a small bug, a papercut, or a scoped improvement that never warranted
a roadmap (Bajutsu Evolution, BE) item — from its issue number to shipped, green code. You are the
**implementer**; the deterministic gate (`make check`) is the judge — never an LLM. The issue body
is your spec. Converse in the user's language; write code, commits, and pull request (PR) text per
the conventions below.

This skill is the plain-issue sibling of [`implement-be`](../../../.apm/skills/implement-be/SKILL.md), which ships a
numbered BE item. Both end in the same place: a self-contained, gate-green change behind an
auto-opened Draft PR, driven to quiet-and-green by the same bounded follow-up loop. They differ in
only two places — how the skill claims ownership, and what closes the loop once the fix merges —
because a plain issue has no BE file to read a `Status` from, no bot-managed tracking issue, and no
`Status` to flip on merge. Everything in between is `implement-be`'s procedure, invoked by
reference rather than restated, so the two skills cannot drift apart. One instruction there has no
counterpart: `implement-be`'s `be-progress-tracker` checkpoints are keyed to a BE id, which a plain
issue has none of — skip them.

## Prime directives (these bound every line you write)

Re-read [`CLAUDE.md`](../../../CLAUDE.md) and [`DESIGN.md`](../../../DESIGN.md) before you touch code.
The fix must honor all three directives, and a fix that brushes any of them is an escalation (step
3), never something to work around quietly:

1. **AI authors and investigates, never judges.** Never add an LLM call to the Tier-2 `run` / CI
   gate ([`docs/glossary.md`](../../../docs/glossary.md#the-two-tiers)). Pass/fail comes only from
   machine-checkable assertions.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector fails
   immediately rather than tapping the first match.
3. **App-agnostic.** Per-app differences live in config (`targets.<name>`); the tool, drivers, and
   runner stay unchanged across targets.

## Workflow

### 1. Resolve the issue

Accept any of: `#123`, a bare `123`, or the issue's full URL, and normalize it to the bare number
before substituting it for `<N>` — an unstripped `#` turns the rest of the command line into a shell
comment. Read the issue itself before anything else — its state, its assignees, and its body:

```bash
gh issue view <N> --json number,title,state,assignees,labels,body
```

**Then explain the issue to the user.** Post a short overview: the number and title, a
plain-language summary of the reported problem in your own words, and the issue's current state
(open or closed, assigned or up for grabs). This orients the user before any judgment, branching, or
code — every run of this skill starts with it, not just the first time.

### 2. Claim the issue

A plain issue is never synced by [BE-0109](../../../roadmaps/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md),
which opens and labels tracking issues for roadmap items alone, so ownership lives on the issue's
own assignee field. The `state` and `assignees` you already read in step 1 are the guard:

- **Closed** — stop. A closed issue is either already fixed or deliberately declined; tell the user
  which the issue says it is, and let the user decide whether to reopen it.
- **Assigned to someone else** (a login that isn't the account `gh` is authenticated as — check with
  `gh api user --jq .login`) — **stop.** The assignee field is how parallel sessions signal
  ownership, so an existing assignee means someone else has picked this issue up. Name the assignee
  and don't branch, plan, or write any code. Continue only if the user explicitly tells you to
  proceed anyway.
- **Open and unassigned, or already assigned to you** — claim it and continue:
  ```bash
  gh issue edit <N> --add-assignee @me
  ```

`--add-assignee @me` assigns the human account `gh` is authenticated as — the same account commits
and PRs are attributed to. Re-assigning yourself is a no-op, so running the claim again on a resumed
session is harmless.

### 3. Judge whether the fix fits this skill

This skill ships fixes that need no design decision, so judge that before planning one. No label
draws this line: a maintainer applying one at filing time cannot see a fix turn out to need a design
call mid-investigation, and the skill can. Hold the fix against a short bar:

- **one clear cause** — the issue names a single defect, not a cluster of related symptoms;
- **a localized change** — the fix stays within a few files that belong to one concern;
- **no new surface** — no user-facing behavior or configuration knob beyond what the issue itself
  already specifies; and
- **no tension with the three prime directives** above.

**When the fix clears the bar,** continue to step 4.

**When it does not,** stop and hand the work back:

1. Tell the user which part of the bar the fix misses, and why.
2. Point at the skill that fits instead —
   [`ideation`](../../../.apm/skills/ideation/SKILL.md) when the design still needs shaping, or
   [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) when the design is settled enough to
   author the proposal and build it in one PR.
3. **Release the claim**, so an escalated issue does not stay marked as in flight:
   ```bash
   gh issue edit <N> --remove-assignee @me
   ```

This valve stays live past this step: a fix's true shape does not always show in the issue body
alone, and the honest moment to escalate is whenever the shape becomes clear — through step 4's
planning stage, and again at step 5 when the self-review surfaces a design change.

### 4. Ground yourself, branch, and confirm a plan

Run [`implement-be`](../../../.apm/skills/implement-be/SKILL.md) steps 3–5 with the issue body in place of a BE
item's *Detailed design*: read the code the issue points at and the tests around it, open a
one-topic branch off the latest `origin/main`, then draft a short plan — the files you will touch,
the machine-checkable outcome that proves the fix works, and the tests that will carry it — and
**confirm that plan with the user before writing code**.

Name the branch for the issue, not for a roadmap item: `claude/fix-issue-<N>-<slug>`. Keep `be-`
out of the branch name. The PR title gate reads the branch as the authoritative roadmap-id signal
([`scripts/lint_pr.py`](../../../scripts/lint_pr.py)), so a branch segment like `be-0123` would demand
a matching `[BE-0123]` title prefix that a plain-issue fix has no id to supply.

### 5. Implement, then review the diff

Run [`implement-be`](../../../.apm/skills/implement-be/SKILL.md) steps 6 and 7: build to the plan matching the
codebase's grain, cover the changed behavior in the fast Python suite, update both language mirrors
of any documentation whose behavior you changed, then self-review the diff against
[`.github/claude-review-prompt.md`](../../../.github/claude-review-prompt.md) as two roles — a
review/plan pass that classifies findings and never edits, and an implement pass that applies its
instructions
([BE-0347](../../../roadmaps/BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md)).
Run the two roles as separate Agent-tool subagents on different models: `opus` for the review/plan
pass, and for the implement pass `sonnet` when the fix stays within `docs/`, `opus` when it touches
product code. The `pr-review-toolkit` plugin supplies the specialized lenses.
One input differs from `implement-be`'s: there is no roadmap `Status` flip pending here, so the
review/plan pass needs no note about one. Keep fixing and re-running a fresh review/plan pass until
it comes back empty, under the 3-round cap that step's
[`references/self-review.md`](../../../.apm/skills/implement-be/references/self-review.md) defines, and don't open the
PR while a real finding still stands. A finding that calls for a genuine design change means the fix
no longer fits this skill:
take step 3's escalation instead — name what the fix now needs, point at `ideation` or
`propose-and-build`, and release the claim — rather than opening the PR.

### 6. Verify — the gate

```bash
make check
```

It must be green; **never push red** (the tracked pre-push hook runs the gate for you). `make check`
— never an AI call — is the sole verdict on the
[Tier-2 gate](../../../docs/glossary.md#the-two-tiers). On-device end-to-end (E2E) runs are a separate,
heavier path outside this gate; drive one only when the fix's correctness genuinely depends on it,
rather than claiming the fix works untested.

### 7. Auto-open a Draft PR

Once step 5's review pass is clear, `make check` is green, and the branch is pushed, **open the PR
yourself**, exactly as
[`implement-be`](../../../.apm/skills/implement-be/SKILL.md) step 10 does and for the same reason: this skill's
output is always a self-contained, gate-green change with every review finding resolved
([BE-0230](../../../roadmaps/BE-0230-hands-free-implement-review-loop/BE-0230-hands-free-implement-review-loop.md)).
Three things differ from a BE-item PR, all of them because the fix carries no roadmap item:

- **No `Status` flip.** `implement-be` step 8 records its item's new state in the same
  change; there is no roadmap file here, so that step has no counterpart. Touch no file under
  `roadmaps/`.
- **A plain scoped title, with no `[BE-NNNN]` prefix** — the shape
  [`docs/ai-development.md`](../../../docs/ai-development.md#pull-requests-title-and-body) already
  documents for a PR with no roadmap item. The title and body are in English, always.
- **`Closes #<N>` in the body**, so merging the PR closes the source issue on its own. That line is
  what closes the loop here, in place of the `Implementing PR` row a BE item records.

Everything else carries over unchanged: write a thorough body from
[the template](../../../.github/PULL_REQUEST_TEMPLATE.md) and close it with the `make check`
verification line; open it as a Draft (`gh pr create --draft`); and open it **Ready for review**
instead, with `--reviewer bajutsu-e2e/steering-committee`, when the fix's diff is purely
documentation or prose. Draft → Ready is the human's call, never the skill's.

### 8. Run bounded PR follow-up

Run [`implement-be`](../../../.apm/skills/implement-be/SKILL.md) steps 11 and 12 against the new PR, unmodified —
including its [`references/pr-followup-loop.md`](../../../.apm/skills/implement-be/references/pr-followup-loop.md),
which is where the stop conditions, escalations, and iteration caps now live, so read it before the
first iteration. `/loop` paces the iterations, and each iteration's
[`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md) work runs in a fresh Agent-tool subagent so the
implementation transcript stays out of it; the loop uses the same merge-conflict check, the same
three stop conditions, the same escalations, and the same iteration caps. Stop when the PR is
quiet-and-green and report it — marking the PR ready is the human's sign-off, not the loop's.

## References

- [`implement-be`](../../../.apm/skills/implement-be/SKILL.md) — the BE-item counterpart this skill mirrors, and
  the source of steps 4–8's procedure.
- [`ideation`](../../../.apm/skills/ideation/SKILL.md) and
  [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) — step 3's two escalation targets.
- [`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md) — the skill step 8's loop runs each iteration.
- [`task-select`](../../../.apm/skills/task-select/SKILL.md) — the read-only survey that ranks plain issues as
  candidates and hands the chosen one to this skill.
- [`CLAUDE.md`](../../../CLAUDE.md) · [`DESIGN.md`](../../../DESIGN.md) — the prime directives every
  change must honor.
- [`.github/claude-review-prompt.md`](../../../.github/claude-review-prompt.md) — the review contract
  step 5 applies to the diff. It advises the author; only `make check` judges.

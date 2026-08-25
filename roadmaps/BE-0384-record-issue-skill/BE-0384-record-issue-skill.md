**English** · [日本語](BE-0384-record-issue-skill-ja.md)

# BE-0384 — Ship a record-issue skill that files minor bugs and improvements as GitHub Issues

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0384](BE-0384-record-issue-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0384") |
| Implementing PR | [#1748](https://github.com/bajutsu-e2e/bajutsu/pull/1748) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item proposes `record-issue`, a skill that turns a minor bug or a small, bounded
improvement noticed during work into a GitHub Issue. It classifies the finding, searches for a
duplicate, drafts a title and body from this repository's own issue templates, and — only after
the invoker gives explicit approval — files the issue with `gh issue create`. The shared procedure
lives in one workflow document so it works two ways: as a standalone skill a person invokes
directly, and as a sub-step another skill (for example `pr-followup`) calls when it notices
something worth flagging but out of the current change's scope. A judge-only skill such as
[`claude-review`](../../.apm/skills/claude-review/SKILL.md) never calls it — that skill
edits and files nothing — so its findings reach `record-issue` only through whatever invoked it.

## Motivation

Bajutsu already draws a line between two weights of idea. A substantial feature goes through the
roadmap (BE) process: [`ideation`](../../.apm/skills/ideation/SKILL.md) drafts a scoped
proposal with a Motivation and a Detailed design, and a human merges it before CI allocates a real
BE ID. A minor defect or a small improvement does not need any of that weight —
[`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml) already says as much:
"Use this issue for a lightweight request or to start the conversation," pointing away from the
roadmap process for anything short of a well-formed design.

Nothing today operationalizes that lightweight side. A contributor or an agent who notices a small
defect while working on something unrelated faces three bad options: fix it inline and enlarge the
current change's scope, keep going and lose track of it, or stop to write an issue by hand — recall
which template applies, search for a duplicate, and phrase the report well. That last option is
correct but has enough friction that a minor finding is more often dropped than filed.

Claude Code's own harness already has a shape for the first half of this problem: the `spawn_task`
tool lets a session flag an out-of-scope finding as a chip the user can act on later, without
derailing the current turn. That chip is ephemeral and scoped to one session — it does not survive
past the session, is not visible to the rest of the team, and is not a GitHub Issue, so
[`task-select`](../../.apm/skills/task-select/SKILL.md) — which already treats open GitHub
Issues as one of its two candidate sources alongside the roadmap — cannot pick it up. `record-issue`
is the durable, repository-level counterpart: filing a persistent, team-visible issue that
`task-select` can surface to anyone, in a later session, on a different machine.

`record-issue` only ever files an issue; it never ships a fix.
[`fix-issue`](../BE-0380-fix-issue-skill/BE-0380-fix-issue-skill.md) is the consuming counterpart
that ships an implementation for an already-filed issue — typically after `task-select` surfaces it
as a candidate — so the two items never overlap in scope.

## Detailed design

**Skill layout.** Following the single-source convention in
[`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout), the skill is one source directory,
`.apm/skills/record-issue/`, holding a `SKILL.md` that carries both the procedure and the
Claude Code invocation it needs, plus a `references/` file for any depth that would push the body
past APM's size budget. `make skills` deploys it to `.claude/skills/record-issue/`, and both the
source and the deployment are committed
([BE-0390](../BE-0390-apm-skill-management/BE-0390-apm-skill-management.md)).

**Inputs.** A description of the finding — either typed directly by the invoker, or handed over by
a calling skill that spotted something out of its own scope — plus whatever supporting context is
available: a file and line, a command that reproduces it, or the environment it showed up in. A
calling skill also states whether a human is in the turn, because the skill cannot observe that
itself — and neither can `pr-followup`, whose steps "behave identically whether run in a subagent
or called directly". Only the loop layer knows: `implement-be` step 12 states it when it hands a
subagent `pr-followup`'s steps, `pr-followup` passes it on to the sub-step invocation, and its
absence means attended. The other input shape is a resumed invocation: an approved new-issue
draft — title, body, and label — from which the skill resumes directly at step 5's
`gh issue create`, since the human already gave explicit approval on a later turn, against the
draft the loop's report showed them. Only an attended turn can carry this input: the skill refuses a
resumed invocation that arrives with the unattended flag set — that flag is its only signal that a
human is in the turn — and returns the draft as a pending draft again rather than filing it, since
resuming with no human present would file the draft with no approval at all, exactly what the
confirmation gate exists to prevent. A comment on one of the candidate
matches that an unattended run carried is the human's own follow-up from there, outside this resume
path.

**Step 1 — classify.** Decide among three landings. `feature_request.yml` only points anything short
of a well-formed design away from the roadmap, so this skill states the concrete bar itself: does
this need a Detailed design, a discussion of trade-offs, or changes across multiple modules? If so,
this is not a minor finding —
stop and point the invoker to `ideation` (or, for a small item whose design is already settled,
`propose-and-build`) instead of filing an issue. With no human in the turn there is no invoker to
point at, so the finding returns in the same pending-draft field the unattended path below uses,
marked as needing a roadmap item rather than an issue — otherwise this one judgment call would cost
the whole finding, the outcome this item exists to prevent. Otherwise, classify the finding as a **bug**
(something behaves other than intended) or a lightweight **enhancement** (a small, bounded
improvement).

**Step 2 — search for a duplicate.** Run `gh issue list --search "<keywords>
-label:roadmap-tracking" --state all --limit 10` with keywords drawn from the finding, and show the
invoker any candidate matches (number, title, state, labels). The search excludes the
`roadmap-tracking` issues the workflow of
[BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) creates and closes
on its own: those are bot-owned, so a comment on one of them is no landing at all. Because that
exclusion also removes the only signal that a BE item already covers the finding, this step
checks the roadmap itself, cheap pass first. The cheap pass is the keyword lookup
[`roadmap-filter`](../../.apm/skills/roadmap-filter/SKILL.md) exists for, `make roadmap-find
ARGS="--grep <keyword>"`, which matches the id, title, `Topic`, and `## Introduction` excerpt of
every item in one run, across all five statuses — reaching the `Deferred` and `Rejected` items a
status-scoped survey would miss, so the skill never files an issue for a finding the roadmap already
parked or decided against. A title-and-introduction match is still coarse, since an item's coverage
of a small finding usually lives in its `Detailed design`, so when the cheap pass returns nothing,
fall back to the open statuses (`make roadmap-status STATUS="Proposal"`, then `"In progress"`) and
grep every returned item's file (the `Path` column names it) for the same keywords. The fallback
stays second because it is the expensive pass — close to 400 items, past 127,000 lines — and inside
the hands-free loop it would otherwise repeat once per finding per iteration. On a hit,
title or body, show the invoker that item and ask whether to stop there instead of drafting
anything — when the item already covers the finding, an issue would only duplicate it — or to
proceed anyway: a keyword match, in a title or an item's body, is not proof of coverage, and only
the invoker can judge it.
When an **open** match looks like the same issue, ask whether to comment on the existing issue
instead of filing a new one, or to proceed anyway — the invoker decides, since only they can judge
whether the match is close enough. Never route a report onto a **closed** match: `task-select`
surveys open
issues only, so a comment there never resurfaces as a candidate. File a new issue linking the
closed one as prior context instead.

**Step 3 — draft.** Read the matching template — `bug_report.yml` for a bug,
`feature_request.yml` for an enhancement — and synthesize a markdown body whose sections mirror
that template's input fields — its `textarea`s and, for `bug_report.yml`, its required `dropdown`,
whose rendered value is one of the options that template declares, verbatim, rather than free prose.
`gh issue create` posts plain markdown rather than rendering the
template's YAML form, so the skill fills those fields itself instead of relying on `gh` to do it.
A template's required `checkboxes` block is the exception: render it as a statement of what the
skill actually verified — step 2's duplicate search and what it covered — never as a pre-ticked
box, since `feature_request.yml`'s three prime-directive confirmations are the invoker's judgment
to give. Pick the matching label (`bug` or `enhancement`); no new label is needed; see
*Alternatives considered*. When the invoker instead chose to comment on an open match in step 2,
draft that comment body separately — the finding, its evidence, and where it was noticed — since
`gh issue comment` carries no title or label of its own. Note the chosen label as a
`gh issue edit --add-label` addition for step 5 only when the target issue carries neither `bug`
nor `enhancement` — step 2 shows each candidate's labels alongside its number, title, and state —
so commenting never re-classifies an issue someone else filed.

**Step 4 — confirm.** Show the invoker everything about to be posted — a new issue's title, body,
and label, or an existing issue's comment body and any label to add — and wait for explicit
approval before posting anything. Filing or commenting on a GitHub Issue publishes content the
whole team sees, so this step runs every time, not only the first time a session uses the skill.

**Step 5 — create and report.** On approval, run `gh issue create --title <title> --body-file
<file> --label <label>` for a new issue, or, for a comment on the match step 2 picked, `gh issue
comment <number> --body-file <comment-file>` followed by `gh issue edit <number> --add-label
<label>` when step 4 approved a label to add. Report the resulting issue URL back to the invoker,
or, when called as a sub-step, back into the calling skill's own output, so a review or follow-up
pass can list what it filed alongside what it fixed inline.

Because the workflow document describes one procedure regardless of caller, a standalone
invocation and a call from another skill follow identical steps — including the confirmation
gate, which no calling skill may skip on the invoker's behalf. A resumed invocation (see *Inputs*)
is not a skip of that gate: its confirmation already ran, on the attended turn where the human
approved the exact draft the loop's report showed, so entering at step 5 executes an approval the
gate already collected. When no human is in the turn —
`pr-followup` running as one iteration of `implement-be`'s hands-free loop (BE-0230) is the case
that matters — the skill files nothing and stalls nothing: it returns the finished draft in a new
**pending-draft** field of the iteration's structured summary. That field is distinct from the
escalation field `implement-be` step 12 already reads — the one `pr-followup` step 4 routes a
self-review-only finding through, and the one the loop treats as a stop signal — so a draft never
stops the loop: the human sees it when the loop reports, and approves it on a later turn. The loop
carries every pending draft its iterations returned into its own final report, deduplicated against
the drafts earlier iterations already returned, because each iteration is a fresh subagent that
re-notices the same finding. The report therefore holds one entry per finding rather than one per
iteration, and a draft returned early survives to the report the human reads.
Step 2's new-issue-or-comment choice, and a
roadmap-filter hit's stop-or-proceed choice, defer the same way rather than being settled
unattended: the skill drafts for a new issue and carries every candidate it found — issue matches
and any roadmap-filter hit — beside the draft, so the human decides whether to approve it, comment
on a candidate by hand instead, or discard the finding. Approval of the drafted issue resumes the
skill directly at step 5 against the draft the human actually saw, rather than re-drafting it. A
pending draft
is deliberately not an escalation: every entry in BE-0230's escalation list stops the loop and
hands the PR to the human, which an incidental out-of-scope note should never do to an
otherwise-healthy follow-up loop.

**Caller and documentation wiring.** A calling skill only runs a sub-step its own workflow names —
`be-progress-tracker` runs because `ideation` and `implement-be` each name it — so `pr-followup`
(and any other calling skill) gains an explicit `record-issue` sub-step. The same change wires the
documentation: `docs/ai-development.md` (and its `docs/ja/` mirror) and `CLAUDE.md`, including the
skill's default `model:` tier (BE-0103).

## Alternatives considered

**File without confirmation.** Rejected: creating an issue is a publishing action visible to the
whole team, and skipping confirmation risks a noisy or misdiagnosed report reaching other
contributors before anyone reviews it.

**Extend `ideation` to also handle minor findings.** Rejected: `ideation`'s contract is authoring a
BE roadmap item — a placeholder ID, a Detailed design, the self-review pass against the CI review
contract. A minor finding needs none of that weight, and folding both into one skill would either
overload `ideation`'s procedure or leave it applied inconsistently across two very different sizes
of idea.

**Reuse `spawn_task`-style ephemeral chips instead of a GitHub Issue.** Rejected: a session-local
chip does not survive past the session, is not visible to the rest of the team, and is not a
candidate source `task-select` reads — so it does not close the gap this item targets.

**Add a new label to mark these issues as lightweight.** Considered, but the absence of the
`roadmap-tracking` label already distinguishes a `record-issue` filing from a BE-linked issue,
and the existing `bug` / `enhancement` labels already say what kind of finding it is. An added label
would add process without adding information.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Author `.apm/skills/record-issue/SKILL.md` (classify → duplicate search → draft →
      confirm → create), and run `make skills` to deploy it to `.claude/skills/record-issue/`.
- [x] Wire the callers: name the `record-issue` sub-step in `pr-followup` (and any other calling
      skill) the way `ideation` / `implement-be` name `be-progress-tracker`.
- [x] Wire the loop layer: `implement-be` step 12 states whether a human is in the turn when it
      hands a subagent `pr-followup`'s steps, and its structured-summary contract gains a
      pending-draft field, kept distinct from the escalation field so a draft never stops the loop.
      Step 12 also carries every pending draft its iterations returned into its own final report,
      deduplicated against earlier iterations' drafts, so a draft returned early in a run still
      reaches the human as one entry rather than one per iteration.
- [x] Documentation wiring: `docs/ai-development.md` (+ ja) and `CLAUDE.md`, including the skill's
      default `model:` tier (BE-0103).
- [x] Verify the standalone path and at least one calling-skill path (for example `pr-followup`
      flagging an out-of-scope finding) both exercise the confirmation gate, that an unattended
      run (`pr-followup` inside `implement-be`'s hands-free loop) returns its draft in the
      iteration's structured summary instead of filing or escalating, and that a human's later
      approval resumes the skill at step 5 against that same draft rather than re-drafting it.

**Log**

- Shipped the skill, its `pr-followup` and `implement-be` wiring, and the documentation. A skill is
  prose with no runtime to exercise, so the last box was checked by reading the three wired texts
  against each other ([#1748](https://github.com/bajutsu-e2e/bajutsu/pull/1748)). Four things had to
  hold:
  - the confirmation step admits no caller-side waiver;
  - the human-in-the-turn statement reaches `record-issue` unchanged, through `pr-followup`;
  - the pending-draft field is named in all three texts, and held apart from the escalation field;
  - a resumed invocation is refused while the statement says no human is in the turn.

## References

- [`.github/ISSUE_TEMPLATE/bug_report.yml`](../../.github/ISSUE_TEMPLATE/bug_report.yml) and
  [`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml) — the templates this
  skill's drafts mirror.
- [`ideation`](../../.apm/skills/ideation/SKILL.md) and
  [`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md) — the counterparts
  for an idea substantial enough to need a Detailed design.
- [`task-select`](../../.apm/skills/task-select/SKILL.md) — already surveys open GitHub
  Issues as a candidate source; the intended consumer of what this skill files.
- [BE-0380](../BE-0380-fix-issue-skill/BE-0380-fix-issue-skill.md) — the consuming counterpart that
  ships a fix for an issue this skill filed.
- [`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout) — the single-source skill convention
  this item's layout follows (BE-0390).

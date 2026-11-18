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

This item proposes `record-issue`, a skill that turns a minor bug or a small, bounded improvement
noticed during work into a GitHub Issue. The skill classifies the finding and searches for a
duplicate. It drafts a title and body from this repository's own issue templates. After the invoker
gives explicit approval, and not before, it files the issue with `gh issue create`.

The shared procedure lives in one workflow document, so the skill works two ways. A person can
invoke it directly as a standalone skill. Another skill (for example `pr-followup`) can call it as a
sub-step when that skill notices something worth flagging that lies outside the current change's
scope. A judge-only skill such as [`claude-review`](../../.apm/skills/claude-review/SKILL.md) never
calls it, because that skill edits and files nothing. Its findings reach `record-issue` only through
whatever invoked it.

## Motivation

Bajutsu already draws a line between two weights of idea. A substantial feature goes through the
roadmap (BE) process. [`ideation`](../../.apm/skills/ideation/SKILL.md) drafts a scoped proposal
with a Motivation and a Detailed design, and a human merges it before CI allocates a real BE ID. A
minor defect or a small improvement needs none of that weight.
[`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml) already says as much: "Use
this issue for a lightweight request or to start the conversation." It points away from the roadmap
process for anything short of a well-formed design.

Nothing today operationalizes that lightweight side. A contributor or an agent who notices a small
defect while working on something unrelated faces three bad options:

- Fix it inline and enlarge the current change's scope.
- Keep going and lose track of it.
- Stop to write an issue by hand: recall which template applies, search for a duplicate, and phrase
  the report well.

The last option is correct but carries enough friction that a minor finding is more often dropped
than filed.

Claude Code's own harness already has a shape for the first half of this problem. The `spawn_task`
tool lets a session flag an out-of-scope finding as a chip the user can act on later, without
derailing the current turn. That chip is ephemeral and scoped to one session. It does not survive
past the session and is not visible to the rest of the team. It is not a GitHub Issue either, and
[`task-select`](../../.apm/skills/task-select/SKILL.md) already treats open GitHub Issues as one of
its two candidate sources alongside the roadmap, so it cannot pick the chip up.
`record-issue` is the durable, repository-level counterpart. It files a persistent, team-visible
issue that `task-select` can surface to anyone, in a later session, on a different machine.

`record-issue` only files an issue and never ships a fix.
[`fix-issue`](../BE-0380-fix-issue-skill/BE-0380-fix-issue-skill.md) is the consuming counterpart
that ships an implementation for an already-filed issue, typically after `task-select` surfaces it
as a candidate. The two items never overlap in scope.

## Detailed design

**Skill layout.** The skill follows the single-source convention in
[`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout). It is one source directory,
`.apm/skills/record-issue/`, holding a `SKILL.md` that carries both the procedure and the Claude Code
invocation it needs. A `references/` file holds any depth that would push the body past APM's size
budget. `make skills` deploys the skill to `.claude/skills/record-issue/`, and both the source and
the deployment are committed
([BE-0390](../BE-0390-apm-skill-management/BE-0390-apm-skill-management.md)).

**Inputs.** The first input is a description of the finding. The invoker types it directly, or a
calling skill hands it over after spotting something outside its own scope. Whatever supporting
context is available accompanies it: a file and line, a command that reproduces the finding, or the
environment it showed up in.

A calling skill also states whether a human is in the turn, because the skill cannot observe that
itself. Neither can `pr-followup`, whose steps "behave identically whether run in a subagent or
called directly". Only the loop layer knows. `implement-be` step 12 states it when it hands a
subagent `pr-followup`'s steps, and `pr-followup` passes it on to the sub-step invocation. Its
absence means attended.

The other input shape is a resumed invocation: an approved new-issue draft with a title, a body, and
a label. The skill resumes directly at step 5's `gh issue create`, because the human already gave
explicit approval on a later turn, against the draft the loop's report showed them.

An attended turn alone can carry this input. The skill refuses a resumed invocation that arrives
with the unattended flag set, since that flag is its sole signal that a human is in the turn. It
returns the draft as a pending draft again instead of filing it. Resuming with no human present
would file the draft with no approval at all, which is exactly what the confirmation gate exists to
prevent.

A comment on one of the candidate matches that an unattended run carried is the human's own
follow-up from there. That comment lies outside this resume path.

**Step 1 — classify.** Decide among three landings. `feature_request.yml` points anything short of a
well-formed design away from the roadmap and says no more, so this skill states the concrete bar
itself: does the finding need a Detailed design, a discussion of trade-offs, or changes across
multiple modules? If so, the finding is not minor. The skill stops and points the invoker to
`ideation` (or, for a small item whose design is already settled, `propose-and-build`) instead of
filing an issue.

With no human in the turn, no invoker is there to point at. The finding then returns in the same
pending-draft field the unattended path below uses, marked as needing a roadmap item rather than an
issue. Without that route, this one judgment call would cost the whole finding, the outcome this
item exists to prevent.

When none of those three conditions holds, classify the finding as a **bug** (something behaves
other than intended) or a lightweight **enhancement** (a small, bounded improvement).

**Step 2 — search for a duplicate.** Run `gh issue list --search "<keywords>
-label:roadmap-tracking" --state all --limit 10` with keywords drawn from the finding. Show the
invoker any candidate matches (number, title, state, labels).

The search excludes the `roadmap-tracking` issues that the workflow of
[BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) creates and closes
on its own. Those issues are bot-owned, so a comment on one of them is no landing at all.

That exclusion also removes the sole signal that a BE item already covers the finding. This step
checks the roadmap itself, in two passes, cheap pass first.

The cheap pass is the keyword lookup that
[`roadmap-filter`](../../.apm/skills/roadmap-filter/SKILL.md) exists for: `make roadmap-find
ARGS="--grep <keyword>"`. One run matches the id, title, `Topic`, and `## Introduction` excerpt of
every item, across all five statuses. It runs first because it answers with each match's id, title,
`Topic`, and status, where the grep pass below returns a path still to open.

A title-and-introduction match is still coarse, since an item's coverage of a small finding usually
lives in its `Detailed design`. The step therefore also greps the item files themselves
(`grep -ril "<keyword>" roadmaps/`). It does so every time, not merely when the cheap pass came back
empty, because a coarse hit on one item says nothing about another item's `Detailed design`.

The grep's keyword has to be narrowed until it discriminates, before its hits reach the choices
below. `roadmaps/` holds close to 400 items across some 770 files and 127,000-plus lines, so a
common word matches nearly all of them. `grep -ril issue roadmaps/` returns essentially every file
there, and a list that size filters nothing. A keyword narrow enough to discriminate returns a short
list of paths rather than the prose behind them. That is what keeps the second pass affordable every
time.

Some findings sit in the roadmap's own core vocabulary, though, where even a faithful phrase returns
dozens of paths. Narrowing past the finding's own wording is not the answer: an over-narrowed grep
that comes back empty is a manufactured miss, indistinguishable from real absence. There the skill
reports the grep pass as **inconclusive**. The report lists the keywords tried and their hit counts,
and never a subset of paths the skill picked. The metadata pass's hits plus that note carry the
choices, because a truncated list would settle a choice the invoker owns on evidence the invoker
never saw.

Both passes span all five statuses, `Deferred` and `Rejected` included. The roadmap deliberately
parked one kind of item and decided against another with no condition expected to reopen it. Those
are the last items to re-file as an issue, and a status-scoped survey is exactly what would miss
them.

Neither pass proves absence, though. A keyword search finds merely the wording it was given, so a
miss never substitutes for the invoker's judgment.

On a hit in a title or a body, show the invoker that item and ask whether to stop there instead of
drafting anything, or to proceed anyway. Stopping fits when the item already covers the finding,
because an issue would merely duplicate it. A keyword match, in a title or an item's body, is not
proof of coverage, and the invoker alone can judge it.

When an **open** match looks like the same issue, ask whether to comment on the existing issue
instead of filing a new one, or to proceed anyway. The invoker decides, since the invoker alone can
judge whether the match is close enough.

Never route a report onto a **closed** match. `task-select` surveys open issues alone, so a comment
there never resurfaces as a candidate. File a new issue that links the closed one as prior context
instead.

**Step 3 — draft.** Read the matching template: `bug_report.yml` for a bug, `feature_request.yml`
for an enhancement. Synthesize a markdown body whose sections mirror that template's input fields.
Those fields are its `textarea`s and, for `bug_report.yml`, its required `dropdown`. The rendered
value of the dropdown is one of the options that template declares, verbatim, rather than free
prose. `gh issue create` posts plain markdown rather than rendering the template's YAML form, so the
skill fills those fields itself instead of relying on `gh` to do it.

A template's required `checkboxes` block is the exception. Render it as a statement of what the
skill actually verified, namely step 2's duplicate search and what it covered. Never render it as a
pre-ticked box, since `feature_request.yml`'s three prime-directive confirmations are the invoker's
judgment to give.

Pick the matching label (`bug` or `enhancement`). No new label is needed; see *Alternatives
considered*.

When the invoker instead chose in step 2 to comment on an open match, draft that comment body
separately: the finding, its evidence, and where it was noticed. `gh issue comment` carries no title
or label of its own. Note the chosen label as a `gh issue edit --add-label` addition for step 5
solely when the target issue carries neither `bug` nor `enhancement`. Step 2 shows each candidate's
labels alongside its number, title, and state, so commenting never re-classifies an issue someone
else filed.

**Step 4 — confirm.** Show the invoker everything about to be posted: a new issue's title, body, and
label, or an existing issue's comment body and any label to add. Wait for explicit approval before
posting anything. Filing or commenting on a GitHub Issue publishes content the whole team sees, so
this step runs every time, not merely the first time a session uses the skill.

**Step 5 — create and report.** On approval, run `gh issue create --title <title> --body-file
<file> --label <label>` for a new issue. For a comment on the match step 2 picked, run `gh issue
comment <number> --body-file <comment-file>`, followed by `gh issue edit <number> --add-label
<label>` when step 4 approved a label to add. Report the resulting issue URL back to the invoker.
When the skill is called as a sub-step, report it back into the calling skill's own output, so a
review or follow-up pass can list what it filed alongside what it fixed inline.

**One procedure for every caller.** The workflow document describes one procedure regardless of
caller. A standalone invocation and a call from another skill therefore follow identical steps,
including the confirmation gate, which no calling skill may skip on the invoker's behalf. A resumed
invocation (see *Inputs*) does not skip that gate either. Its confirmation already ran, on the
attended turn where the human approved the exact draft the loop's report showed, so entering at step
5 executes an approval the gate already collected.

**No human in the turn.** The case that matters is `pr-followup` running as one iteration of
`implement-be`'s hands-free loop (BE-0230). The skill then files nothing and stalls nothing. It
returns the finished draft in a new **pending-draft** field of the iteration's structured summary.

That field is distinct from the escalation field `implement-be` step 12 already reads. The
escalation field is the one `pr-followup` step 4 routes a self-review-only finding through, and the
loop treats it as a stop signal. A draft therefore never stops the loop: the human sees it when the
loop reports, and approves it on a later turn.

The loop carries every pending draft its iterations returned into its own final report,
deduplicated against the drafts earlier iterations already returned, because each iteration is a
fresh subagent that re-notices the same finding. The report holds one entry per finding rather than
one per iteration, and a draft returned early survives to the report the human reads.

Step 2's new-issue-or-comment choice, and a roadmap hit's stop-or-proceed choice, defer the same way
instead of being settled unattended. The skill drafts for a new issue. Beside the draft it carries
every candidate it found: issue matches and every roadmap hit, from the metadata pass and the grep
alike (an inconclusive grep contributes its keywords-and-counts note in place of hits). The human
then decides whether to approve the draft, comment on a candidate by hand instead, or discard the
finding.

Approval of the drafted issue resumes the skill directly at step 5 against the draft the human
actually saw, rather than re-drafting it. A step 1 escalation entry carries no issue draft, so it is
not resumable at all. Approving it starts `ideation` or `propose-and-build` on the finding, never a
re-entry at step 5.

A pending draft is deliberately not an escalation. Every entry in BE-0230's escalation list stops
the loop and hands the PR to the human, which an incidental out-of-scope note should never do to an
otherwise-healthy follow-up loop.

**Caller and documentation wiring.** A calling skill runs a sub-step only when its own workflow
names it. `be-progress-tracker` runs because `ideation` and `implement-be` each name it. In the same
way, `pr-followup` (and any other calling skill) gains an explicit `record-issue` sub-step. The same
change wires the documentation: `docs/ai-development.md` (and its `docs/ja/` mirror) and
`CLAUDE.md`, including the skill's default `model:` tier (BE-0103).

## Alternatives considered

**File without confirmation.** Rejected: creating an issue is a publishing action visible to the
whole team, and skipping confirmation risks a noisy or misdiagnosed report reaching other
contributors before anyone reviews it.

**Extend `ideation` to also handle minor findings.** Rejected: `ideation`'s contract is authoring a
BE roadmap item — a placeholder ID, a Detailed design, the self-review pass against the CI review
contract. A minor finding needs none of that weight. Folding both into one skill would either
overload `ideation`'s procedure or leave it applied inconsistently across two very different sizes
of idea.

**Reuse `spawn_task`-style ephemeral chips instead of a GitHub Issue.** Rejected: a session-local
chip does not survive past the session, is not visible to the rest of the team, and is not a
candidate source `task-select` reads. It does not close the gap this item targets.

**Add a new label to mark these issues as lightweight.** Considered, but the absence of the
`roadmap-tracking` label already distinguishes a `record-issue` filing from a BE-linked issue. The
existing `bug` / `enhancement` labels already say what kind of finding it is. An added label would
add process without adding information.

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

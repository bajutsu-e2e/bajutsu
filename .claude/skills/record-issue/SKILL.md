---
name: record-issue
model: sonnet
description: File a minor bug or a small, bounded improvement as a GitHub Issue — classify the finding, search for a duplicate, draft from this repository's issue templates, and create it only after explicit approval.
---

# Record a finding as a GitHub Issue

Turn a minor bug or a small, bounded improvement — noticed while working on something else — into a
GitHub Issue. This skill classifies the finding, searches for a duplicate, drafts a title and body
from this repository's own issue templates, and files the issue only after the invoker gives
explicit approval. It **files an issue and nothing more**: it never fixes the finding, never opens a
branch, and never touches product code
([BE-0384](../../../roadmaps/BE-0384-record-issue-skill/BE-0384-record-issue-skill.md)).

The procedure below is the same whether a person invokes the skill directly or another skill calls
it as a sub-step after spotting something outside its own scope —
[`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md) is the caller wired today. A calling skill may not skip any
step on the invoker's behalf, least of all step 4's confirmation.

Two counterparts bound the scope. An idea substantial enough to need a design goes to
[`ideation`](../../../.apm/skills/ideation/SKILL.md) or [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) instead,
which step 1 decides. An issue already filed is shipped by
[`fix-issue`](../../../.apm/skills/fix-issue/SKILL.md), typically after
[`task-select`](../../../.apm/skills/task-select/SKILL.md) surfaces the issue as a candidate — so what this skill
files is what those two skills later consume.

## Inputs

A **description of the finding**, typed by the invoker or handed over by a calling skill, plus
whatever supporting context exists: a file and line, a command that reproduces the finding, or the
environment where the finding showed up.

A calling skill also states **whether a human is in the turn**, because this skill cannot observe
the answer itself. Only the loop layer knows: [`implement-be`](../../../.apm/skills/implement-be/SKILL.md) step 12
states it when handing a subagent `pr-followup`'s steps, and `pr-followup` passes the statement on
to its own sub-step invocation. **An absent statement means a human is present** — an attended turn
is the default, and the unattended path below runs only when a caller says so explicitly.

One other input shape exists: a **resumed invocation**, carrying an approved new-issue draft (its
title, body, and label) produced by an earlier unattended run. A resumed invocation enters directly
at step 5, because the human already gave explicit approval against the exact draft the loop's
report showed. Only an attended turn may carry a resumed invocation: **refuse a resumed invocation
that arrives with the unattended statement set**, and return the draft as a pending draft again
rather than filing it. Resuming with no human present would file a draft nobody approved, which is
what step 4 exists to prevent.

## Steps

### 1. Classify the finding

Decide among three landings. The first decision is whether the finding belongs here at all. Hold it
against a concrete bar — does the finding need a Detailed design, a discussion of trade-offs, or
changes across multiple modules? If any of the three holds, the finding is not minor: **stop and
point the invoker at [`ideation`](../../../.apm/skills/ideation/SKILL.md)**, or at
[`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) when the design is already settled enough to
author and build in one pull request (PR). File nothing.

Otherwise classify the finding as one of two kinds, which decide the template and the label that
step 3 uses:

- a **bug** — something behaves other than intended;
- an **enhancement** — a small, bounded improvement.

### 2. Search for a duplicate

Draw keywords from the finding and search the issues:

```bash
gh issue list --search "<keywords> -label:roadmap-tracking" --state all --limit 10
```

Show the invoker every candidate match, with its number, title, state, **and labels** — step 3 needs
the labels. The search excludes the `roadmap-tracking` issues that
[BE-0109](../../../roadmaps/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)'s
sync opens and closes on its own, because those issues are bot-owned: a comment on one of them is no
landing at all.

That exclusion also removes the only signal that an open roadmap item already covers the finding, so
check the roadmap directly. [`roadmap-filter`](../../../.apm/skills/roadmap-filter/SKILL.md) accepts a single status
per run, so run it twice:

```bash
make roadmap-status STATUS="Proposal"
make roadmap-status STATUS="In progress"
```

Match the returned titles on the same keywords. A title is a coarse filter — an item usually covers
a small finding in its `Detailed design` rather than in its title — so also grep every returned
item's file, named by the table's `Path` column, for those keywords, not only the items whose title
already matched.

Two choices come out of this step, and the invoker makes both:

- **A roadmap hit, in a title or a body.** Show the invoker the item and ask whether to stop there
  rather than draft anything, since an issue would only duplicate an item that already covers the
  finding, or to proceed anyway. A keyword match is not proof of coverage, and only the invoker can
  judge whether the item really covers the finding.
- **An open issue that looks like the same finding.** Ask whether to comment on the existing issue
  instead of filing a new one, or to proceed anyway. Only the invoker can judge whether the match is
  close enough.

**Never route a report onto a closed match.** `task-select` surveys open issues only, so a comment
on a closed issue never resurfaces as a candidate. File a new issue that links the closed one as
prior context instead.

### 3. Draft the issue

Read the template that matches step 1's classification —
[`bug_report.yml`](../../../.github/ISSUE_TEMPLATE/bug_report.yml) for a bug,
[`feature_request.yml`](../../../.github/ISSUE_TEMPLATE/feature_request.yml) for an enhancement —
and synthesize a Markdown body whose sections mirror that template's input fields. `gh issue create`
posts plain Markdown rather than rendering the template's YAML form, so the skill fills those fields
itself instead of relying on `gh` to do it:

- **Every `textarea`** becomes a section under the field's own label.
- **`bug_report.yml`'s required `dropdown`** becomes a line carrying one of the options that
  template declares, **verbatim**, rather than free prose.
- **A required `checkboxes` block** is the exception to mirroring. Render it as a statement of what
  the skill actually verified — step 2's duplicate search and what the search covered — never as a
  pre-ticked box. `feature_request.yml`'s three prime-directive confirmations are the invoker's
  judgment to give, not the skill's.

Pick the matching label, `bug` or `enhancement`. No new label is needed: the absence of
`roadmap-tracking` already distinguishes a `record-issue` filing from a roadmap-linked issue.

When step 2's invoker chose to comment on an open match instead, draft that comment body separately
— the finding, its evidence, and where it was noticed — because `gh issue comment` carries no title
or label of its own. Note the chosen label as a `gh issue edit --add-label` addition for step 5
**only when the target issue carries neither `bug` nor `enhancement`** (step 2 already showed each
candidate's labels), so commenting never re-classifies an issue someone else filed.

### 4. Confirm before posting

Show the invoker everything about to be posted — a new issue's title, body, and label, or an
existing issue's comment body and any label to add — and **wait for explicit approval**. Filing or
commenting on a GitHub Issue publishes content the whole team sees, so this step runs every time,
not only the first time a session uses the skill, and no calling skill may waive it.

### 5. Create and report

On approval, file the new issue:

```bash
gh issue create --title "<title>" --body-file <file> --label <label>
```

Or, for a comment on the match step 2 picked, post the comment and add the label only when step 4
approved one:

```bash
gh issue comment <number> --body-file <comment-file>
gh issue edit <number> --add-label <label>
```

Report the resulting issue URL back to the invoker. When this skill ran as a sub-step, report the
URL into the calling skill's own output, so a review or follow-up pass lists what it filed alongside
what it fixed inline.

## When no human is in the turn

`pr-followup` running as one iteration of `implement-be`'s hands-free loop
([BE-0230](../../../roadmaps/BE-0230-hands-free-implement-review-loop/BE-0230-hands-free-implement-review-loop.md))
is the case that matters. There the skill **files nothing and stalls nothing**: it finishes the
draft and returns the draft in the **pending-draft** field of that iteration's structured summary.

Entering step 5 from a resumed invocation is not a skip of step 4's gate. The confirmation already
ran, on the attended turn where the human approved the exact draft the loop's report showed, so
step 5 executes an approval the gate had already collected.

Three rules keep the unattended path honest:

- **A pending draft is not an escalation.** Every entry in `implement-be`'s escalation list stops
  the loop and hands the PR to the human, which an incidental out-of-scope note should never do to
  an otherwise-healthy follow-up loop. The pending-draft field stays distinct from the escalation
  field for that reason.
- **Defer step 2's choices rather than settling them.** Draft for a **new issue**, and carry every
  candidate the search found — issue matches and any roadmap-filter hit — beside the draft. The
  human then decides whether to approve the draft, comment on a candidate by hand instead, or
  discard the finding. Approval resumes this skill at step 5 against the draft the human actually
  saw, rather than re-drafting it.
- **A comment on a candidate is the human's own follow-up**, made from the report, outside this
  skill's resume path.

## What this skill does NOT do

- Fix the finding, open a branch, or touch product code
- File or comment without the invoker's explicit approval
- Create a roadmap (Bajutsu Evolution, BE) item, or decide a design question
- Comment on a `roadmap-tracking` issue, or on any closed issue
- Add a label to an issue that already carries `bug` or `enhancement`

## References

- [`.github/ISSUE_TEMPLATE/bug_report.yml`](../../../.github/ISSUE_TEMPLATE/bug_report.yml) and
  [`feature_request.yml`](../../../.github/ISSUE_TEMPLATE/feature_request.yml) — the templates
  step 3's drafts mirror.
- [`ideation`](../../../.apm/skills/ideation/SKILL.md) and [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) —
  step 1's two escalation targets, for a finding substantial enough to need a Detailed design.
- [`roadmap-filter`](../../../.apm/skills/roadmap-filter/SKILL.md) — the read-only survey step 2 runs against the
  roadmap.
- [`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md) — the calling skill wired today, and the source of the
  unattended turn's pending-draft path.
- [`task-select`](../../../.apm/skills/task-select/SKILL.md) and [`fix-issue`](../../../.apm/skills/fix-issue/SKILL.md) — the consumers
  that rank a filed issue as a candidate and then ship it.
- [`CLAUDE.md`](../../../CLAUDE.md) · [`DESIGN.md`](../../../DESIGN.md) — the prime directives every
  change must honor. Nothing here touches the `run` / CI verdict.

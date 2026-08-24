---
name: be-progress-tracker
model: haiku
description: Create or update a per-BE-item status Artifact (overview, implementation progress, work log). Called by ideation, implement-be, or propose-and-build at their own step boundaries — not run standalone.
---

# BE progress tracker

Maintain one glanceable, continuously updated status page per BE item while another BE workflow
(`ideation`, `implement-be`, `propose-and-build`, or a future one) does the real work. This skill
only records and displays; it never authors code, proposals, roadmap files, or PR content, and it
never decides anything the calling workflow hasn't already decided itself.

## Scope: transcription, not judgment

This is deliberately a **cheap, mechanical** skill: every call formats decisions the calling
workflow already made into a short status document. If it ever finds itself deciding something —
which step comes next, whether a design is sound, what the item's `Status` should be — that is a
sign it has drifted into the calling workflow's job; stop and hand the decision back. Keep the
document terse: it is a live dashboard for the human watching the session, not a rewrite of the
roadmap item or the PR body, and it is never the source of truth for either.

## When it runs

Called by another BE workflow's own step boundaries — never started for its own sake. The calling
workflow hands it, at each checkpoint:

- the BE id, or the `BE-XXXX` placeholder before allocation;
- the calling workflow's name and the step it just completed or is now entering;
- one short sentence of what happened — the work-log line;
- the handle of the page an earlier checkpoint already created for this id, when there is one, so
  this call updates that page instead of starting a second one.

A calling workflow decides for itself which of its own steps are worth a checkpoint — typically the
same boundaries that already warrant a user-facing update (a branch created, a plan confirmed, code
written, a review pass clear, the gate green, a PR opened, one follow-up-loop iteration). Skipping a
checkpoint that would add no new information is expected, not an error.

Dispatch each checkpoint through the Agent tool with `model: "haiku"` passed explicitly — a
subagent call does not inherit this skill's own frontmatter model. The job is transcription and
formatting of decisions the caller already made, not new judgment, so the cheapest capable model is
the right default; a caller can still upshift for an item whose overview is unusually hard to
summarize.

## What the status page holds

Three sections, in this fixed order:

1. **Overview** — the item's id, title, `Status`, and a short plain-language summary of what it
   proposes and why. Read from the roadmap item's own files
   (`roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md`) once they exist; before allocation, read from
   whatever draft the calling workflow has produced so far.
2. **Progress** — a checklist mirroring the calling workflow's own step list (e.g. `implement-be`'s
   numbered steps, or `ideation`'s), each marked done, in progress, or pending.
3. **Work log** — short timestamped entries, newest first, one line per checkpoint. Never rewrite
   or delete a past entry; only append.

## How to update

- **First call for a given BE id in a session** — read the roadmap item if it exists yet, for the
  Overview; seed Progress with every step of the calling workflow, ticking only the ones already
  done; start the Work log with one entry.
- **Every later call carries no memory of the earlier ones** — each checkpoint runs as its own
  subagent, so it knows only what this call was handed, not what an earlier checkpoint wrote. Read the existing page first and carry its Progress ticks and Work log forward verbatim,
  advancing Progress and appending exactly one new Work log line on top of them. Rebuilding the page
  from this call's input alone would silently drop every earlier entry — that is the one failure
  this step exists to prevent. When the existing page can't be read, say so in the page itself
  rather than quietly starting a fresh log.
- **Never invent status.** If the calling workflow hasn't reported a step as done, leave it pending
  — don't infer it from what "usually" happens next.

## Output

The page is a Markdown Artifact, published and later redeployed with the Artifact tool. Load
`artifact-design` before the first write and keep the design plain — this is a glanceable dashboard
for the human watching the session, not a polished deliverable.

**One Artifact per BE item, updated in place.** On the first call for a BE id in a session, check
whether one already exists — `Artifact({action: "list"})`, or a URL the calling workflow cached at
an earlier checkpoint — before publishing a new one; every later call redeploys to that same URL by
passing the same `file_path` rather than creating a fresh Artifact per checkpoint. Hand the URL back
to the calling workflow so it can reuse it next time. Pick the `favicon` and `title` on the first
call and keep both stable across every redeploy of the same item, per the Artifact tool's own rule;
only a hard pivot in what the item is — which shouldn't happen mid-implementation — would justify
changing them.

## Non-goals

- **Never a source of truth.** The roadmap item's own files remain canonical for `Status` and
  `Implementing PR`; the PR remains canonical for the code and its review state. If this page and
  either of those ever disagree, the roadmap item and the PR are right, not this page.
- **Never blocks the calling workflow.** If a checkpoint call fails, or the host has no cheap way to
  make it, the calling workflow notes that tracking didn't update and continues regardless — this
  skill is a convenience, never a gate.
- **Never edits** roadmap files, code, commits, or the PR. It only ever writes its own status page.

## References

- [`implement-be`](../implement-be/SKILL.md), [`ideation`](../ideation/SKILL.md),
  [`propose-and-build`](../propose-and-build/SKILL.md) — the workflows that call this one at
  their own step boundaries; `propose-and-build` inherits the checkpoints of the two it delegates
  to rather than defining its own.

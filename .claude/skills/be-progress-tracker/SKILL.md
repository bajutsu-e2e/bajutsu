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
- the roadmap item's own repo-relative path (`roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md`), once it
  exists — omitted before allocation, since the file (and its slug) don't exist yet;
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

## The template (verbatim, every call)

**Read [`references/template.html`](references/template.html) now and copy it verbatim** as the
Artifact's source file. It is exactly this shape — four headers, in this order, nothing added and
nothing renamed, `<style>` block included; the only things that change between calls are the
placeholder content, the one status class, and whether the roadmap-link button is present, all
noted in the field rules below.

Field rules, all mandatory:

- **`{ID}`** — the BE id (`BE-0123`) or the `BE-XXXX` placeholder, verbatim, always plain text in
  the `<h1>`. **`{Title}`** — the roadmap item's own title once it exists, else the calling
  workflow's current working title.
- **Roadmap-link button** — once the calling workflow has handed over the roadmap item's
  repo-relative path, include the `<a class="link-btn" href="https://github.com/bajutsu-e2e/bajutsu/blob/main/{that path}">Roadmap item ↗</a>`
  button verbatim, right after the status badge — it needs its own border/hover styling to read as
  clickable, unlike the badge next to it. Before allocation — no path handed over yet — drop the
  button entirely rather than pointing it at a path that doesn't exist on `main` yet.
- **Meta line** — the badge, the roadmap-link button when present, then exactly the two remaining
  fields in that order, joined by ` · `, nothing else ever added to it.
  - `Status` — the roadmap item's own `Status:` field, copied verbatim, once
    `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md` exists; before allocation, literally
    `Proposal (pre-allocation)`. Its badge gets exactly one of three classes, chosen only by the
    text itself — never a judgment call: `done` when `Status` is exactly `Implemented`, `progress`
    when it is exactly `In progress`, `neutral` for every other value (`Proposal`,
    `Proposal (pre-allocation)`, `Deferred`, `Rejected`).
  - `Workflow` — the name of the workflow making *this* call (`implement-be`, `ideation`, …), not
    the skill that ends up shipping the item — this field changes mid-page when
    `propose-and-build` hands off between the two.
  - `{timestamp}` — always `YYYY-MM-DD HH:MM UTC`, from `date -u +"%Y-%m-%d %H:%M UTC"`. Never a
    relative time ("5 min ago"), never a different format, never the local timezone.
- **Overview** — rewritten in full from the current source (the roadmap file, or the calling
  workflow's draft) each call; it is a snapshot, not an append log. Read from
  `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md` once it exists; before allocation, read from
  whatever draft the calling workflow has produced so far.
- **Progress** — one `<li>` per step of the calling workflow's own step list (e.g. `implement-be`'s
  numbered steps, or `ideation`'s), numbered to match that list, with the step's title copied
  verbatim — never paraphrased or shortened. Exactly three line shapes, no other suffix, class, or
  annotation is ever added:
  - done: `<li class="done"><span class="mark">✓</span> {n}. {title}</li>`
  - in progress: `<li class="active"><span class="mark">▶</span> {n}. {title} — in progress</li>`
  - pending: `<li class="pending"><span class="mark">○</span> {n}. {title}</li>`
- **A checkpoint that reports a *unit within* a step** — `implement-be`'s step 6 checks in once
  per plan unit — leaves that step's line at `active` / `— in progress` and records the unit in the
  Work log alone. Never add a sub-list, a unit count, or any extra Progress line for it.
- **Work log** — newest entry first (prepend, don't append at the bottom). One `<li>` per
  checkpoint, each shaped exactly `<li><time>{timestamp}</time>{one sentence}.</li>` — a single
  sentence, past tense, ending in a period, no line breaks inside it. Never rewrite or delete a
  past entry.

## How to update

- **First call for a given BE id in a session** — read the roadmap item if it exists yet, for the
  Overview; seed Progress with every step of the calling workflow, ticking only the ones already
  done; start the Work log with one entry.
- **Every later call carries no memory of the earlier ones** — each checkpoint runs as its own
  subagent, so it knows only what this call was handed, not what an earlier checkpoint wrote. Read
  the existing page first (`Artifact({action: "read", url})`) and carry its Progress `<li>`
  classes and Work log entries forward verbatim, advancing Progress and prepending exactly one new
  Work log `<li>` above them. Rebuilding the page from this call's input alone would silently drop
  every earlier entry — that is the one failure this step exists to prevent. When the existing page
  can't be read, say so as a Work log line — for example
  `<li><time>{timestamp}</time>Could not read the existing page; entries before this point may be missing.</li>`
  — rather than quietly starting a fresh log.
- **Never invent status.** If the calling workflow hasn't reported a step as done, leave it pending
  — don't infer it from what "usually" happens next.
- **Never add a section, a field, or a line shape not in the template above.** If a call has
  information that doesn't fit an Overview/Progress/Work-log line, drop it rather than growing the
  template — a caller with a real recurring need should change this skill, not the one call.

## Output

The page is an HTML Artifact, published and later redeployed with the Artifact tool. Load
`artifact-design` before the first write. `references/template.html` is already the whole design —
a color-coded status badge and progress marks so the human watching the session can scan state at a
glance; fill its placeholders and don't add anything beyond them. It is still a live dashboard, not
a polished deliverable: color and decoration exist to speed up scanning, not to grow the page.

**One Artifact per BE item, updated in place.** On the first call for a BE id in a session, check
whether one already exists — `Artifact({action: "list"})`, or a URL the calling workflow cached at
an earlier checkpoint — before publishing a new one; every later call redeploys to that same URL by
passing the same `file_path` rather than creating a fresh Artifact per checkpoint. Hand the URL back
to the calling workflow so it can reuse it next time. Pick the `favicon` on the first call and keep
it stable across every redeploy of the same item, per the Artifact tool's own rule; only a hard
pivot in what the item is — which shouldn't happen mid-implementation — would justify changing it.
The `<title>` tag in the template already supplies the Artifact's title each redeploy, so no
separate `title` param is needed.

## Non-goals

- **Never a source of truth.** The roadmap item's own files remain canonical for `Status` and
  `Implementing PR`; the PR remains canonical for the code and its review state. If this page and
  either of those ever disagree, the roadmap item and the PR are right, not this page.
- **Never blocks the calling workflow.** If a checkpoint call fails, or the host has no cheap way to
  make it, the calling workflow notes that tracking didn't update and continues regardless — this
  skill is a convenience, never a gate.
- **Never edits** roadmap files, code, commits, or the PR. It only ever writes its own status page.

## References

- [`references/template.html`](references/template.html) — the Artifact template, copied verbatim
  every call; see "The template" above.
- [`implement-be`](../../../.apm/skills/implement-be/SKILL.md), [`ideation`](../../../.apm/skills/ideation/SKILL.md),
  [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) — the workflows that call this one at
  their own step boundaries; `propose-and-build` inherits the checkpoints of the two it delegates
  to rather than defining its own.
